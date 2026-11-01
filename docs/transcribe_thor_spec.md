# `/transcribe` chord/key/fingering interface — spec for the Thor side

Status: **implemented + verified** (media-pipeline, 2026-10-31).
This is the contract for exposing the chord/key/fingering surface of
`POST /transcribe` on Thor. All of it is **deterministic post-processing**
(worker stage 5.5) — the LLM never sees or touches any of it. Binding design
principle: tab data is deterministic from the audio; the LLM only labels
sections / cleans ASR lyrics / writes confidence notes (validated, with a
deterministic fallback — `polish_fallback: true`).

## 1. Payload fields

| Field | Type | Default | Validation (400 at submit) |
|---|---|---|---|
| `path` | str | required | non-empty; media_jobs-relative or absolute host path |
| `instrument` | str | `"guitar"` | v1: `guitar` only |
| `tuning` | str | `"standard"` | v1: `standard` only |
| `lyrics` | bool | `true` | — |
| `stems` | bool | `true` | — (vocal-stem ASR; demucs failure falls back to full mix + caveat) |
| `full_notes` | bool | `false` | — |
| `whisper_model` | str | null | falls back to `MEDIA_TRANSCRIBE_WHISPER_MODEL` (large-v3) |
| `transpose_semitones` | int | `0` | integer in −12..12 (bool rejected) |
| `target_key` | str | null | tonic `^[A-G][#b]?m?$` (e.g. `"E"`, `"Bb"`, `"F#m"`); offset computed from the **detected** key, shortest direction (±6); **wins over** `transpose_semitones` if both given |
| `chord_style` | enum | `"as_detected"` | `as_detected` \| `triads` \| `power` (case-insensitive) |
| `capo` | bool | `false` | bool only |
| `shape_hints` | bool | `true` | bool only |

Defaults are **faithful to the recording** — every modification is opt-in.

## 2. Transform pipeline (order matters)

1. **Transpose** — `transpose_semitones` or `target_key` (offset from detected
   key). Shifts note events, chord symbols (root shifted, quality preserved)
   and the key label. `notes.json` + `song.mid` are rewritten from the
   shifted events so the MIDI matches the transposed analysis; the
   string/fret mapping runs on the transposed events (tab in the new key,
   natural fingerings). Lyrics/BPM/section boundaries are time-based and
   unchanged.
2. **`chord_style`** — transforms chord symbols:
   - `as_detected`: as detected (7ths/9ths/6ths, major/minor)
   - `triads`: 7th/9th/6th → triad (`Dmaj7→D`, `Bm7→Bm`, `G6→G`; dim/aug/sus kept)
   - `power`: everything → `X5` (root + 5th, mode-agnostic — `Dmaj7→D5`,
     `A#m7→A#5`). Also a *correction* for metal: a recorded power chord has
     no 3rd, so the CQT detector's major/minor guess is arbitrary; the root
     is still solid.
3. **`capo`** — when the (transposed) key has no open shapes, suggests the
   smallest capo (0–5) landing the key on a friendly open-shape key
   (G/C/D/A/E/Bm/Em/Am). Chords are then shown as the **finger shapes**
   (shifted down by the capo), like a capo tab site. Independent of
   `chord_style` (a capo is a player preference, not a fact about the song).
4. **`shape_hints`** — annotates every chord with its easiest fingering,
   computed on the **final finger chord** (after transpose + capo — what you
   actually play).

## 3. Shape-hint algorithm (deterministic)

Inputs: finger-chord root (pitch class `pc`), `chord_style`. The chord
**symbol carries the mode** — a minor symbol + "E-shape barre @ 4" means the
minor E-shape barre; sus/dim chords use the major-form basic shape.

**Open-shape set** = roots with a standard nut shape (major & minor):
`{C, D, E, F, G, A, B}` (pc 0,2,4,5,7,9,11). The sharp roots
`{C#, D#, F#, G#, A#}` (pc 1,3,6,8,10) have no open shape → barre.

- **`power`:** E-fret = (pc−4) mod 12 (root on 6th string), A-fret =
  (pc−9) mod 12 (root on 5th string) → pick the lower fret (tie → E).
  fret 0 = open E5/A5.
- **`as_detected` / `triads`:** if pc in the open set → `open`; else the
  E-fret/A-fret rule above → barre.

### Shape object (`analysis.json`, per chord event)

```json
{"type": "open",  "base": null, "fret": null}
{"type": "barre", "base": "E", "fret": 4}
{"type": "barre", "base": "A", "fret": 1}
{"type": "power", "base": "A", "fret": 0}
{"type": "power", "base": "E", "fret": 3}
```

`shape: null` when the symbol is unparseable (shouldn't happen) or
`shape_hints: false` (field absent).

### Rendering (`tab.md`, per chord on the section `Chords:` line)

| Shape | Rendered |
|---|---|
| `{open}` | `open` |
| `{barre, E, 4}` | `E-shape barre @ 4` |
| `{barre, A, 1}` | `A-shape barre @ 1` |
| `{power, A, 0}` | `open A5` |
| `{power, E, 3}` | `power @ 3 (E-shape)` |

Example: `Chords: A#m7 [A-shape barre @ 1] | G#maj7 [E-shape barre @ 4]`

**v1 scope:** per-chord *easiest* shape (open if available, else lowest-fret
barre/power). A global "consistent position" pass (keep the whole
progression in one hand position, like a capo for barres) is a v2
refinement — deliberately not in v1.

## 4. Output schema

`analysis.json` (v1 schema) — top-level fields added by this surface:

| Field | Type | Meaning |
|---|---|---|
| `transpose_semitones` | int | the applied offset (0 = none) |
| `chord_style` | str | the requested style (echoed) |
| `capo` | bool | the requested flag (echoed) |
| `shape_hints` | bool | the requested flag (echoed) |
| `capo_suggestion` | `{capo, finger_key}` \| null | non-null only when a capo was actually applied (key had no open shapes) |

Each chord event (per section): `{time_s, symbol, confidence, shape?}` —
`shape` present only when `shape_hints: true`.

Caveats (human-readable, in `caveats`):
- `transposed ±n semitones (detected X -> Y)`
- `capo n — chords shown as X finger shapes (sounding key Y)`
- `chords shown as power chords (root + 5th) — idiomatic for metal/hard rock; mode (major/minor) of the detection is not reliable on power-chord material`
- `chords simplified to triads (chord_style=triads)`

`tab.md` header lines: `Transposed ±n semitones from the source
recording` / `Capo: n — chords shown as X finger shapes (sounding key Y)` /
`Chords shown as power chords (root + 5th)` / `Chords simplified to triads`.

## Examples — Riptide (detected C# major; chords A#m7–G#maj7–C#maj7–F#maj7)

| Config | A# | G# | C# | F# |
|---|---|---|---|---|
| `as_detected` (default), no capo | `A#m7` A-barre@1 | `G#maj7` E-barre@4 | `C#maj7` A-barre@4 | `F#maj7` E-barre@2 |
| `power`, no capo | `A#5` power@1 (A) | `G#5` power@4 (E) | `C#5` power@4 (A) | `F#5` power@2 (E) |
| `power`, capo 1 | `A5` open A5 | `G5` power@3 (E) | `C5` power@3 (A) | `F5` power@1 (E) |
| `triads`, no capo | `A#m` A-barre@1 | `G#` E-barre@4 | `C#` A-barre@4 | `F#` E-barre@2 |

Transpose examples (same track): `target_key: "E"` → +3 → E major
(progression C#m7–Bmaj7–Emaj7, all 537 MIDI notes exactly +3);
`transpose_semitones: -3` → A# major; `target_key: "Gb"` → +5 → F# major
(enharmonic).

## Error handling

- All payload validation above → **clean 400 at submit** (before job
  creation). Worker re-validates (dies with a distinct message) as a backstop.
- Distinct job errors: no decodable audio track, `audio too short` (<10 s),
  whisper model missing.
- The job is **never blocked** on the LLM (polish) or on demucs/stems
  (fallback to full-mix ASR + caveat).

## Verified (2026-10-31, Riptide m4a via API + worker)

- All 4 chord_style/capo combinations: correct symbols, shapes, headers,
  caveats; `transcribe_qa_check.py` PASS in every mode.
- Shape hints: unit-tested for all 12 pitch classes × both styles (fret math
  sanity: hinted fret reproduces the root on the stated string);
  `shape_hints: false` omits the field cleanly.
- Transpose: positive/negative/target_key/enharmonic; `song.mid` +
  `notes.json` exactly shifted.
- 400 paths: bad `chord_style`, non-bool `shape_hints`/`capo`, out-of-range
  `transpose_semitones`, bad `target_key`, bool-as-int.
- Helper unit tests: shift_key/shift_chord_symbol round-trip identity,
  simplify_chord, power_chord, suggest_capo across all 24 keys.