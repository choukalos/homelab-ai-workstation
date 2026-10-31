# media-pipeline handoff: `/transcribe` flow (song → guitar tab + lyrics)

> **2026-10-31 (new flow).** Add one job flow to the media-pipeline service
> (:8189): `POST /transcribe` — audio/video file → guitar transcription
> (6-string, standard tuning) + timestamped lyrics. Companion planning doc on
> the Thor side: `docs/song_transcribe_plan.md`.
>
> **What this is.** A single-file handoff for the GPU host. You already run
> the pipeline (job queue, GPU locking, VRAM budgeting, metering, VLLM
> matrix-coder, ffmpeg). This file specifies one new flow + one new worker.
> The remote (Thor) side stays a thin client — it handles YouTube ingest
> (yt-dlp) and file upload; the pipeline receives a **file path in
> `media_jobs`** and does everything else.
>
> **Design principle (binding):** the tab *data* (string/fret per note,
> chords, sections, note events) is computed **deterministically** from the
> audio — reproducible and verifiable. The LLM step only labels sections,
> cleans ASR lyrics, and writes confidence notes. The LLM never produces or
> alters a tab line; the worker diff-checks and falls back to the
> deterministic rendering if the LLM output fails validation. The job is
> never blocked on the LLM step.

---

## Table of contents
- [§1. Endpoint contract](#1-endpoint-contract)
- [§2. Worker stages](#2-worker-stages)
- [§3. Output files](#3-output-files)
- [§4. `analysis.json` schema](#4-analysisjson-schema)
- [§5. Guitar mapping rules (deterministic)](#5-guitar-mapping-rules-deterministic)
- [§6. Dependencies, models, budgets](#6-dependencies-models-budgets)
- [§7. Config / env vars](#7-config--env-vars)
- [§8. Metering](#8-metering)
- [§9. QA checklist](#9-qa-checklist)
- [§10. Troubleshooting notes](#10-troubleshooting-notes)

---

## 1. Endpoint contract

`POST /transcribe` — **job flow** (queued like every other flow; 503 +
`retry_after_seconds` when the queue is full; `user`/`client` stamped in the
payload like all flows).

**Input (JSON):**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `path` | str | — (required) | Media file under `media_jobs` (audio or video — the worker extracts the audio track). |
| `instrument` | str | `"guitar"` | v1: `"guitar"` only. Any other value → **400** `{"error": "v1 supports instrument=guitar only"}`. |
| `tuning` | str | `"standard"` | v1: `"standard"` (EADGBE) only. Any other value → 400. |
| `lyrics` | bool | `true` | Run the ASR stage. `false` skips stage 5 (faster; `lyrics.txt` is written empty with a header note). |
| `stems` | bool | `false` | Run demucs vocal-stem separation first (cleaner lyrics on dense mixes; +1–2 min). |
| `whisper_model` | str | env `MEDIA_TRANSCRIBE_WHISPER_MODEL` (`large-v3`) | faster-whisper model name. |

**`output` on done:**

```json
{
  "analysis": "<job>/analysis.json",
  "tab":      "<job>/tab.md",
  "lyrics":   "<job>/lyrics.txt",
  "midi":     "<job>/song.mid",
  "audio":    "<job>/audio.wav"
}
```

All five keys always present (a `lyrics: false` job still writes the other
four; `lyrics.txt` carries a `# lyrics disabled` header).

**Errors:** standard job `error` string. Distinct cases to surface clearly:
no decodable audio track; audio shorter than 10 s (`"audio too short"`);
whisper model missing and not downloadable; LLM polish unavailable (→ NOT an
error — see §2 stage 7 fallback; the job still succeeds).

**Job budget:** timeout 30 min (a 5-minute song should finish in ~3–8 min).

---

## 2. Worker stages

One worker process per job, stages run sequentially in the job dir:

```
0. ingest      ffmpeg: decode source → audio.wav (44.1 kHz, stereo, PCM s16)
1. global      librosa: duration, key (Krumhansl–Schmuckler profile fit over
              CQT), BPM (tempo tracking, median over the track)
2. notes       basic-pitch (ONNX): polyphonic note events
              → song.mid (full events) + notes.json
3. chords      CQT chord-template matching (beat-aligned, see §5.3)
4. sections    deterministic segmentation (see §5.4)
5. lyrics      [if stems] demucs htdemucs → vocal stem
              [if lyrics] faster-whisper (word timestamps, VAD on) → raw lines
6. mapping     deterministic guitar mapping (see §5) → tab data in analysis.json
7. polish      matrix-coder VLLM (same access as /storyboard): section labels,
              lyrics cleanup, confidence notes → merged + diff-checked
```

Stage details:

- **0. ingest** — `ffmpeg -i <source> -ar 44100 -ac 2 -c:a pcm_s16le audio.wav`.
  Video sources: audio track only. If ffprobe finds no audio stream → job
  error.
- **1. global** — key: fit the 24 Krumhansl–Schmuckler major/minor profiles
  against the track's pitch-class distribution (from stage 2's events or CQT);
  report as e.g. `"E minor"`. BPM: librosa beat tracking, median tempo
  (guard against half/double-time: if 2× or ½× fits the beat grid better,
  prefer the value in 60–180 BPM).
- **2. notes** — `basic-pitch` (Spotify), default settings, `force_mono=False`
  if stereo. Emit every event with `midi`, `start_s`, `end_s`, `freq_hz`,
  `amplitude` (velocity proxy). Write `song.mid` (full polyphonic) and
  `notes.json` (the event list — this is the raw material for verification).
- **3. chords** — see §5.3.
- **4. sections** — see §5.4.
- **5. lyrics** — `stems=true`: demucs `htdemucs` → `vocals.wav` (keep in job
  dir). ASR: `faster-whisper`, `word_timestamps=True`, `vad_filter=True`,
  beam_size=5. Input = `vocals.wav` (stems) or `audio.wav`. Output: lines
  `{start_s, end_s, text, words:[{start_s,end_s,word}]}`.
- **6. mapping** — §5.1/§5.2: assign every note a role (lead / accompaniment /
  bass) and a string+fret; build the per-section tab data.
- **7. polish** — single matrix-coder call (thinking OFF, like the vision
  server's batches). **Input** (compact — never the full note dump):
  ```json
  {"title": null, "key": "E minor", "bpm": 128,
   "sections": [{"index": 0, "start_s": 0, "end_s": 12.3,
                 "chords": ["Em","C","G","D"],
                 "lead_preview": "E5 G5 B4 E5 ...",      // condensed lead notes
                 "raw_lyrics": ["...line 1...", "..."]}]}
  ```
  **Requested output (strict JSON):**
  ```json
  {"title": "...", "artist": "...",
   "sections": [{"index": 0, "label": "intro",
                 "lyrics": [{"start_s": 12.4, "end_s": 15.0, "text": "..."}],
                 "note": "optional one-liner"}],
   "confidence": "high|medium|low", "caveats": ["..."]}
  ```
  **Validation (reject the whole LLM output → fallback):** sections array has
  exactly the same count/order as input; every lyrics line's `start_s` matches
  an input raw line within ±1.0 s and the text is a *cleaning* of it (no new
  words added — word-set must be a subset of the input words, case/accent
  insensitive); `confidence`/`caveats` well-formed. On rejection: ship the
  deterministic `tab.md` (sections labeled `section N`, raw ASR lyrics,
  `confidence` from the DSP heuristics, `caveats: ["LLM polish unavailable —
  raw output"]`). Log the rejection in the job output
  (`"polish_fallback": true`).

**`tab.md` rendering (deterministic code, not LLM):**

```markdown
# <title or "Untitled"> — guitar tab

Artist: <artist or "unknown"> | Key: E minor | BPM: 128 | Tuning: Standard (EADGBE)
Source: <original path/URL as given in the job payload>
Confidence: medium — dense mix; lead/accompaniment separation is heuristic.
Raw data: analysis.json / song.mid (verify lines against these).

## [Intro] (0:00–0:12)

Chords: Em | C | G | D

   e|-------------------------------------------------|
   B|---------------------------------0---------------|
   G|---0--------------------------------------------|
   D|-------0----------------------------------------|
   A|---0-------------0-----------------------------|
   E|-0-----------------0---------------------------|

Lyrics: (none — instrumental)
```

One block per section. Tab grid: 16th-note resolution at the section BPM;
lead notes rendered as fret numbers (open = `0`); accompaniment notes
rendered dimmed (prefix `·` in plain markdown, or a second "all notes" block
per section when the owner wants the full picture — v1: lead only + a
`full_notes: true` job input flag, default false). Chord symbols above the
block, aligned to their beat.

---

## 3. Output files

| File | Producer | Notes |
|---|---|---|
| `audio.wav` | stage 0 | 44.1 kHz stereo s16 (~105 MB / 5 min) |
| `song.mid` | stage 2 | Full polyphonic events (standard MIDI) |
| `analysis.json` | stages 1–7 | The structured contract (§4) |
| `lyrics.txt` | stage 5 | `[mm:ss.d] text` per line (empty + header if `lyrics=false`) |
| `tab.md` | stage 7 (or fallback) | Human-readable artifact |

All under the job dir (14-day retention like every other flow). `vocals.wav`
(stems) and `notes.json` are intermediates — kept in the job dir, not in the
`output` mapping.

---

## 4. `analysis.json` schema

```json
{
  "version": 1,
  "source": "<path as given in the job payload>",
  "instrument": "guitar",
  "tuning": "standard",
  "duration_s": 234.5,
  "key": "E minor",
  "bpm": 128,
  "stems_used": false,
  "polish_fallback": false,
  "confidence": "medium",
  "caveats": ["dense mix — lead/accompaniment separation is heuristic"],
  "sections": [
    {
      "index": 0,
      "label": "intro",
      "start_s": 0.0,
      "end_s": 12.3,
      "chords": [
        {"time_s": 0.0, "symbol": "Em", "confidence": 0.93}
      ],
      "notes": [
        {"midi": 64, "start_s": 0.0, "end_s": 0.42, "freq_hz": 329.6,
         "amplitude": 0.81, "role": "lead",
         "tab": {"string": 4, "fret": 5}}
      ],
      "lyrics": [
        {"start_s": 12.4, "end_s": 15.0, "text": "...",
         "words": [{"start_s": 12.4, "end_s": 12.9, "word": "..."}]}
      ]
    }
  ],
  "chord_progression": "Em - C - G - D"
}
```

Rules:
- `notes` = every basic-pitch event (all roles); `tab` is present on every
  note with `role != "bass"` (bass notes carry `"tab": null`).
- `chords` = one entry per detected chord change (beat-aligned).
- `lyrics` = per-section ASR lines (empty array for instrumental sections).
- `chord_progression` = the most common loop across sections (v1: the
  chorus/verse loop with the most chord events).
- Determinism: for a given input file + settings, `notes`, `chords`,
  section boundaries, and all `tab` mappings are **byte-identical across
  runs** (no randomness; the LLM may only change `label`, cleaned `lyrics`
  text, `confidence`, `caveats`, `title`, `artist`, `note`).

---

## 5. Guitar mapping rules (deterministic)

### 5.1 Role classification

For each note event (MIDI number `m`):

- `m < 45` (below G2 ≈ 98 Hz) → `bass` (excluded from the tab; used only as
  context for the LLM polish input).
- else if monophonic at its onset (no other note starting within 50 ms) and
  `45 ≤ m ≤ 84` (G2–C5, the guitar lead range) → `lead`.
- else (`m > 84`, or polyphonic onset cluster) → `accompaniment`.

Refinement pass: within a section, if a run of `accompaniment` notes is
strictly monophonic for ≥ 4 consecutive notes with direction changes
(melodic contour), reclassify as `lead`. (Catches lead lines that start
mid-chord.)

### 5.2 String/fret assignment

Standard tuning, open-string MIDI: `E2=40, A2=45, D3=50, G3=55, B3=59, E4=64`
(strings 6→1). For a note `m`:

1. Candidates = all `(string, fret)` with `open[string] + fret == m` and
   `0 ≤ fret ≤ MEDIA_TRANSCRIBE_MAX_FRET` (default 15).
2. Score each candidate (lower = better):
   `fret` (prefer low frets)
   + `2 × |preferred_string - string|` where `preferred_string` = the string
     whose open pitch is closest to `m` without exceeding it (high notes go
     to high strings)
   + `3 × |fret - last_fret_on_same_string|` (hand continuity — only when the
     previous note was on the same string and within 1 s)
   + `100` if `fret > 12` (avoid stretch positions)
3. Pick the minimum; ties → lower string number.
4. Assert the invariant (fail the job loudly if ever violated):
   `open[string] + fret == m`.

### 5.3 Chord detection

- CQT: 44.1 kHz, n_fft=2048, hop=512, 36 bins/octave, 96 bins.
- Chord set (v1): `maj, min, maj7, min7, dom7, dim, aug, sus2, sus4, 6`.
  Templates = pitch-class sets (root + chord tones; 7ths/9ths optional at
  0.5 weight).
- At each beat (from stage 1's beat grid): score each of the 120 templates
  (10 qualities × 12 roots) = sum of CQT magnitudes at the template's pitch
  classes over the beat window; `confidence = template_score / total_energy`.
- Keep a chord only if it changes from the previous one (no flapping:
  require ≥ 1 beat between changes; a new chord must beat the incumbent by a
  margin of 10% of total energy to replace it).
- Report `symbol` with standard notation (`Em`, `C`, `G7`, `Dsus4`, `Bmaj7`,
  `F#dim`, ...).

### 5.4 Section segmentation

- Onset envelope: spectral flux (hop 512), smoothed (50 ms window).
- Candidate boundaries: local minima of the energy envelope where the
  surrounding onset energy drops below 20% of the local mean for ≥ 0.5 s.
- Merge candidates closer than `MEDIA_TRANSCRIBE_MIN_SECTION_S` (default 4 s);
  drop sections shorter than 2 s (merge into the neighbor).
- Boundaries are deterministic; **labels** (`intro`/`verse`/`chorus`/`bridge`/
  `outro`/`solo`) come from stage 7 (LLM), with fallback `section N`.
  Label hints passed to the LLM: chord-loop similarity between sections
  (sections sharing the same chord loop get the same label when possible) and
  lyrics presence (sections with lyrics are verse/chorus candidates).

---

## 6. Dependencies, models, budgets

**New pip deps** (pipeline env, Python 3.10+):

| Package | Use | Notes |
|---|---|---|
| `librosa` | stages 1, 3, 4 | CPU; pulls soundfile/scipy |
| `basic-pitch` | stage 2 | ONNX Runtime; CPU or GPU |
| `faster-whisper` | stage 5 | CTranslate2; GPU (CUDA) preferred |
| `demucs` | stage 5 (optional) | `htdemucs` only; GPU |
| `pretty_midi` | stage 2 | MIDI writing (or `mido`) |

**Model downloads (one-time, ~4 GB disk total):**
- faster-whisper `large-v3` (~3 GB, CTranslate2 format) — cached in the
  pipeline's model dir.
- demucs `htdemucs` (~800 MB) — only if `stems` is ever used; lazy-download
  on first use (or pre-fetch at deploy).
- basic-pitch ONNX (~15 MB).

**Per-job budget (4–5 min song):**

| Stage | Time (GPU) | VRAM |
|---|---|---|
| ingest (ffmpeg) | <10 s | — |
| global (librosa) | ~10 s | — |
| basic-pitch | 30–60 s | ~1 GB (or CPU) |
| chords (CQT) | ~15 s | — |
| sections | ~5 s | — |
| demucs (if `stems`) | 60–120 s | ~4 GB |
| whisper large-v3 | 30–60 s | ~3 GB |
| LLM polish (matrix-coder) | 30–60 s | existing vLLM serving |
| **Total** | **~3–8 min** | peak ~4 GB (stems) |

Fits the existing GPU-lock/VRAM-budget machinery; stages run sequentially in
one job. If the GPU is unavailable (locked by another job's model), the queue
already serializes it.

---

## 7. Config / env vars

| Env | Default | Meaning |
|---|---|---|
| `MEDIA_TRANSCRIBE_WHISPER_MODEL` | `large-v3` | faster-whisper model |
| `MEDIA_TRANSCRIBE_WHISPER_DEVICE` | `cuda` | `cuda` \| `cpu` (CPU fallback: ~5–10× slower ASR) |
| `MEDIA_TRANSCRIBE_MAX_FRET` | `15` | Fret cap for tab mapping |
| `MEDIA_TRANSCRIBE_MIN_SECTION_S` | `4` | Minimum section length (s) |
| `MEDIA_TRANSCRIBE_DEMUCS_MODEL` | `htdemucs` | Demucs model (stems stage) |
| `MEDIA_TRANSCRIBE_TIMEOUT_S` | `1800` | Per-job budget (30 min) |

---

## 8. Metering

- New work-unit kind **`transcribe`**: `audio_seconds × rate`, emitted in
  `jobs.jsonl` + `/metrics` like the existing kinds
  (`media_cost_usd_total{stage="transcribe"}`).
- **Placeholder rate: $3.1e-5/s** (the existing audio rate — see Thor
  `METRICS.md`, "Media Work Metering (v2)"). Calibrate after the first real
  runs (energy-based, same procedure as the 2026-09-09 calibration) and
  report the measured rate back to Thor.
- The LLM polish call routes through the LiteLLM proxy with the caller's key
  (storyboard pattern) → counted in LLM $ on the Thor side, **not** in media
  $ (no double counting — permanent rule).

---

## 9. QA checklist

Run on the GPU host; all must pass before the Thor side is built.

1. `GET /health` — unchanged shape; new flow does not alter queue fields.
2. **Happy path:** submit a known song (clean recording, simple structure —
   e.g. a folk/rock song with a published tab) via
   `POST /transcribe {"path": "<media_jobs file>", "instrument": "guitar"}`
   → job `done` in <10 min; all 5 output keys present; files non-empty.
3. **Schema:** `analysis.json` validates against §4 (write a small checker
   script in the pipeline repo — assert: every section has `chords`/`notes`;
   every non-bass note has a `tab`; every `tab` satisfies
   `open[string] + fret == midi`, `1 ≤ string ≤ 6`, `0 ≤ fret ≤ MAX_FRET`;
   lyrics `start_s` within section bounds).
4. **Key/BPM:** on a song with a known key/BPM (check a reference), reported
   `key` and `bpm` are correct (BPM within ±5%).
5. **Chord accuracy:** on a clean I–IV–V–I recording in a known key, the
   detected progression matches 100% (allow one extra passing chord per 16
   bars at most).
6. **Determinism:** run the same input twice → `notes`, `chords`, section
   boundaries, and all `tab` mappings byte-identical (diff the relevant
   `analysis.json` fields; LLM fields may differ).
7. **Lyrics:** on a clean-vocal song, spot-check 10 lines against known
   lyrics — target ≥90% word accuracy; timestamp drift < 0.5 s.
8. **Stems:** one `stems: true` run on a dense mix — job completes,
   `vocals.wav` exists, lyrics quality visibly better (qualitative).
9. **LLM fallback:** force the polish call to fail (e.g. point the LLM URL at
   a dead port in a test env) → job still `done`, `polish_fallback: true`,
   `tab.md` deterministic version present.
10. **Errors:** no-audio file → clean job error; 5 s clip → `"audio too
    short"`; `instrument: "piano"` → HTTP 400 at submit time.
11. **Metering:** `jobs.jsonl` shows the `transcribe` work unit with
    `user`/`client` stamped; `/metrics` exposes
    `media_cost_usd_total{stage="transcribe"}`.
12. **Regression:** one existing flow (e.g. `POST /tts`) still completes
    normally after the change.

---

## 10. Troubleshooting notes

- **basic-pitch smears on heavy distortion / pitch-shift:** expected; the
  output's `caveats` should mention it when the event list has >30%
  same-onset polyphonic clusters in the lead range.
- **Whisper hallucinates on long instrumentals:** VAD on + `word_timestamps`
  already mitigates; if a section has >10 s with no onsets, suppress its
  lyrics lines (known whisper failure mode).
- **Half/double-time BPM:** stage 1's beat-grid guard handles 60–180 BPM;
  out-of-range songs report the raw tracking value + a caveat.
- **Queue full (503):** unchanged behavior — the Thor client backs off with
  `retry_after_seconds`.
- **Job registry eviction:** unchanged — finished files persist 14 days and
  are retrievable by path (`/files/{name}`, `/dl_token`) even after the job
  id is evicted.
- **Disk:** `audio.wav` + `vocals.wav` are the big intermediates (~110–210 MB
  per job). The existing 14-day sweep covers them; no new retention config.