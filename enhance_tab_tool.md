# Enhance the tab tool (`tab_pdf.py`) — missing shapes + solo-lane rework

> Planning doc. Canonical generator: `mcp/servers/media/tab_pdf.py` (used
> in-process by the `mcp_media` container's `media_render_tab_pdf` tool and
> by the standalone `homelab-tab-pdf` image, `scripts/tab-pdf/`).
>
> Trigger: 4 tabs published 2026-11-05 (Sweet Child O' Mine, Hurt,
> Californication, One) — several chords rendered as **"NO SHAPE / n/a"**
> because the open-shape library doesn't cover them, and the SOLO lane
> doesn't show note durations or techniques (hammer-ons, slides, …).

---

## A. Missing chord shapes

### A1. Confirmed missing (verified in the published PDFs)

Scanned all four published PDFs for `NO SHAPE` entries (pypdf text extract):

| Song | Chord(s) rendered "NO SHAPE" |
|---|---|
| Guns N' Roses — Sweet Child O' Mine | **B6** |
| Johnny Cash — Hurt | — (all shapes present) |
| RHCP — Californication | **F6** |
| U2 — One | **F6, G6** |

Root cause: the pipeline's shape hint for these chords is
`{type: "open", base: null, fret: null}` — it says "open position" but
carries no fret data, so `chord_frets()` falls back to the hand-maintained
`OPEN_CHORDS` dict, which has no entry → renderer prints name + "n/a".
(Other unknown symbols like C#maj7/G#7 render fine because the pipeline
gave them `barre`/`power` shape hints, which the generator synthesizes.)

**Fix — add to `OPEN_CHORDS` (frets 6→1; verified by interval math):**

| Chord | Frets | Tones produced | Check |
|---|---|---|---|
| **B6** | `x26442` | B D# F# G# | exact B6 (A6 shape x04220 + 2 frets) |
| **F6** | `133211` | F A C (6th implied) | standard open F6 |
| **G6** | `320000` | G B D E | exact G6 (open G, 1st string open adds the 6th) |

### A2. Recommended additions (same class of bug, not yet hit)

Verified by the interval checker (`/tmp/check_shapes.py` — promote to
`mcp/servers/media/test_chord_shapes.py`):

| Chord | Frets | Notes |
|---|---|---|
| D6 | `xx0212` | D A E F# — standard D6 (D6/9 voicing, 6th implied) |
| E6 | `022100` | E G# B — standard E6 (6th implied) |
| A9 | `x02020` | A E F# — standard A9 (7th implied) |
| D9 | `xx0232` | D A C F# — standard D9 |
| Emaj9 | `020100` | E G C# — standard Emaj9 |
| G9 | `353233` | G D F A — verified; **cross-check vs a reference at merge** |
| Am9 | `x02012` | A C E G F# — common open voicing; **cross-check at merge** |

**Rejected candidates** (failed interval verification — keep this list so
nobody re-proposes them blindly):

| Chord | Shape | Why rejected |
|---|---|---|
| G9 | `353533` | tones = G C D F → that's G7sus4, not G9 (no 9th at all) |
| E9 | `025100` | tones = E G G# B → that's E6 (4th string 5th fret is G, not A) |
| Bm9 | `x23210` | tones = B F A C E → F natural = b5; not a Bm9 voicing |

### A3. Structural fixes (kill the whole "n/a" class, not just 3 entries)

1. **Synthesized-barre fallback.** When a symbol is unknown *and* the shape
   hint is `open`, synthesize a barre shape instead of "n/a": find the root
   fret on the 5th string (A-shape) and 6th string (E-shape), pick the
   lower/closer one, reuse the existing `_barre_frets()` generator, and
   label it `barre @ N (synthesized)`. The quality suffix (m/7/9/6/maj7)
   is already parsed by `is_minor()`; extend it to handle 7/9/6 for the
   barre variant (or keep major/minor only and label accordingly).
2. **Report coverage in the render result.** `render()` returns a dict —
   add `missing_shapes: [...]` (symbols that fell through to the fallback
   or n/a) so the `media_render_tab_pdf` MCP tool can warn the caller
   instead of silently shipping "n/a" pages.
3. **Unit-test the shape library.** `test_chord_shapes.py`: for every
   `OPEN_CHORDS` entry, frets → note names → assert (a) root present,
   (b) no tone outside the chord's theoretical tones + allowed extensions
   (13th/6th for 9th chords, 9th for 6th chords). This is what caught the
   G9/E9/Bm9 errors above — a 30-line deterministic test.
4. **Optional: data-file the library.** Move `OPEN_CHORDS` to
   `chord_shapes.json` next to the generator so shape additions are a
   data change (easier review, no code edits). Still needs a container
   rebuild either way; do only if it simplifies the test in (3).

Note on SCOM: the pipeline detected the song in **B major** (common
reference: A major). If it's ever re-transcribed with `target_key="A"`,
the chord set shifts to A6/F#6/Bmaj7/… — the library above covers the
natural outcomes either way.

---

## B. SOLO lane rework (note durations + techniques)

### B1. Current state (why it's weak)

`_solo_cell()` renders each measure as 6 fixed rows (one per string),
each row ≤ 12 fret digits, consecutive repeats collapsed (`1111111` →
`11111`). It uses **only `start_s`** per note. Consequences:

- no idea **how long** to hold a note (the repeat-collapse actively
  destroys duration info),
- no **technique** info (hammer-on? pull-off? slide? separate strokes?),
- all 6 strings shown even when only 2 are used → hard to read,
- no beat grid, no dynamics.

### B2. Data already available (no pipeline change needed for v1)

Lead note events in `analysis.json` carry:

| Field | Use |
|---|---|
| `start_s`, `end_s` | **note duration** (already there — just unused!) |
| `tab.string`, `tab.fret` | placement |
| `amplitude` | dynamics (optional, v2) |
| `freq_hz` | single value per note → **not enough for bend detection** (see B4) |

### B3. Design — per-measure fretboard boards (chord-diagram visual language)

Each measure in the SOLO lane gets a **mini fretboard board** — the same
visual language as the CHORDS grid (string lines, fret lines, orange
dots, starting-fret annotation) — but the dots are **notes placed at
(string, fret)**, with **duration**, **time order**, and **technique
connectors** (hammer-on / pull-off / slide / hold). Vector rendering
(positioned divs or inline SVG — crisp in the PDF, no raster images),
replacing the monospace `<pre>` tab cells. All deterministic
post-processing on the note events (same pattern as the chord stage —
no LLM involved).

**Why a board instead of tab digits:** the reader already knows how to
read the CHORDS diagrams on page 1; the solo board is "the same kind of
board, but the dots are notes in time order, with connectors showing how
to connect them." It encodes all four dimensions (string, fret, time,
duration) natively, which tab digits can't.

**Geometry** (per measure, reusing the chord-diagram CSS):
- 6 vertical string lines (low E left — same as the chord diagram),
  fret lines horizontal.
- **Fret window**: min→max fret used in that measure (padded by 1,
  clamped to 4–7 lines) + a **starting-fret annotation** (`4f`) when the
  window doesn't start at the nut — exactly like the barre chord
  diagrams.
- Fixed board size (~40pt × 50pt, same as the chord tokens) → 7 boards
  per row, measure-aligned with the VOCALS / RHYTHM lanes.
- Strings with no notes in that measure drawn dim (light gray).

**Note glyphs** (one dot per note event):
- Dot at (string, fret) — same dot style as the chord diagram.
- **Dot size ∝ duration** (`end_s − start_s` in beats; clamped 3–8pt)
  — "how long to play it" at a glance.
- **Time-order badge** (1, 2, 3…) beside the dot — the playing order
  within the measure.
- **Duration tail (ON by default)**: thin line extending right from
  the dot, length ∝ beats — the primary "how long to play it" cue,
  alongside the dot size.
- Same (string, fret) repeats: dots stacked with a small vertical
  offset + `×n` count.

**Technique connectors** (between consecutive notes in time order;
deterministic rules, `beat = bar/4`):

| Condition | Inference | Board rendering |
|---|---|---|
| next.start ≤ cur.end (overlap), fret **up** | hammer-on | line dot A → dot B, `h` badge at midpoint |
| next.start ≤ cur.end (overlap), fret **down** | pull-off | line, `p` badge |
| 0 < gap < 0.25·beat | slide (legato) | connector line with a small **arrow icon** at the midpoint, pointing up/down (direction) |
| gap ≥ 0.25·beat | separate strokes | no connector |
| same fret, overlapping | hold / vibrato candidate | small **wavy-line (sine) icon** beside the dot (low confidence) |
| fret jump > 4 with a gap | position shift | no mark (avoid noise) |

Hammer-on / pull-off keep the standard `h` / `p` text badges; slide and
hold/vibrato use small **vector glyph icons** (inline SVG paths baked
into the generator — no external assets, no LLM) so the boards stay
clean and visually consistent at any zoom.

Thresholds (0.25·beat etc.) go in named constants with a comment —
tune against the 4 QA songs.

**Timing**: a 4-beat ruler under each board (4/4 — same assumption the
VOCALS lane already makes); time-order badges + dot size carry the
within-measure timing.

**Fret-window overflow**: if a measure's note range exceeds 7 frets,
split the measure into two side-by-side boards (low-fret window /
high-fret window) with a boundary tick. Deterministic rule, documented.

**Dense-measure fallback**: if a measure exceeds 12 lead notes (or
collisions exceed the stack offset), fall back to the current compact
text tab for that measure only — a deterministic guard that keeps the
layout stable.

**Fingering (finger numbers)**: not inferable from the data (human
judgment). v2: deterministic heuristic (index on the lowest fret of a
group, adjacent fingers for adjacent frets) with optional validated LLM
polish; out of scope for Phase 3.

**Solo key (standard key, mandatory)**: a dedicated key panel at the
top of the SCORE part, so anyone reading the tab can decode the boards
without prior context. Two parts:
1. **Example board** — a small mock board with one of each element,
   labeled with leader lines: a dot with duration tail + time-order
   badge, an `h` connector, a `p` connector, a slide arrow icon, a
   vibrato wavy icon, a dimmed (unused) string, and the 4-beat ruler.
2. **Mark table** — dot size / tail = hold time · number = play order
   · `h` = hammer-on · `p` = pull-off · arrow = slide (direction =
   arrow) · wavy line = hold/vibrato (inferred) · dim string = unused
   in this measure · starting-fret label (e.g. `4f`) = first fret
   shown.

**Dynamics (v2, optional)**: `amplitude` percentile → dot color/weight
for soft notes.

**Confidence footer** (already in the page footer): techniques are
inferred from timing/fret patterns — medium confidence; verify against
the recording.

### B4. What the current data CANNOT do (and where the fix lives)

**Bends and vibrato.** A bend is a pitch ramp *inside* one note event;
the DSP emits a single `freq_hz` per note, so bends/vibrato are invisible
to `tab_pdf.py` today. Detecting them requires the pipeline's DSP (on the
**GPU host / Matrix**, stage 4/5) to emit a pitch contour per note
(e.g. freq at onset/mid/offset, or a small contour array). That's a
pipeline-side change; `tab_pdf.py` just renders whatever it's given.
Track as a separate work item (see Phase 4) — don't scope it into the
tab_pdf rework.

### B5. Rendering notes

- Board = positioned divs (consistent with the chord diagram) or inline
  SVG — decide at implementation time; WeasyPrint handles both (SVG:
  line/circle/path/text). Connector lines: thin rotated div
  (`transform: rotate`) or SVG path.
- Technique icons (slide arrow, vibrato wavy line) are inline SVG paths
  in the generator — deterministic, no asset files.
- CSS reuse: `.ct-*` classes for strings/frets/dots; new `.sb-*`
  (solo-board) classes for badges, tails, ruler, dimmed strings.
- Keep `MEASURES_PER_ROW = 7` and the measure alignment with
  VOCALS/RHYTHM.
- Config flag `solo_style` as a **string enum** (`enum.Enum`; members
  `board` (default) and `text`) — an enum so new members (e.g.
  `both`) can be added later without breaking callers. No `both` for
  now: the text tab serves only as the dense-measure fallback, not a
  parallel view.

---

## C. Deployment & rollout

**Where things live** (answers the "does this need Matrix?": **no**):

| Piece | Location | Role |
|---|---|---|
| `tab_pdf.py` (the code being changed) | this repo, `mcp/servers/media/` | canonical source |
| `mcp_media` container (renders in-process) | **thor** (media host) | `media_render_tab_pdf` tool |
| Job artifacts (analysis.json, tab.md, lyrics.txt) | **Matrix** (GPU host, 192.168.4.55) | fetched by mcp_media itself — untouched |
| `publish_file` skill (blog drop zone) | **thor** | republish with `overwrite=true` |

**Deploy path** (no SSH from the Mac; thor doesn't auto-sync the repo):

1. Commit + push to `github.com/choukalos/homelab-ai-harness`.
2. On thor: `cd /home/chuck/homelab && git pull && docker compose -f compose/compose.mcp.yml build mcp_media && docker compose -f compose/compose.mcp.yml up -d mcp_media` (or `./homelab.sh rebuild mcp`).
3. Re-render (artifacts already on the GPU host — no re-transcription):
   `media_render_tab_pdf(job_id, title, artist, slug)` per song.
4. Republish: `publish_file` skill, `subdirectory=tabs`,
   `overwrite=true`, same slug-prefixed names.
5. QA: standalone `homelab-tab-pdf` image + poppler (`pdftotext`,
   `pdffonts`) for regression; verify zero `NO SHAPE` entries and visual
   check of the new solo lane on all 4 songs.

**QA artifacts** (job IDs from the 2026-11-05 run):
`7184c0486ef7` SCOM · `5b159972e526` Hurt · `65083db9057d` Californication
· `f6b1d2fb2f98` One.

### Phases

| Phase | Scope | Effort |
|---|---|---|
| **1 — bugfix** | A1 (B6/F6/G6) + A2 safe set (D6/E6/A9/D9) → re-render + republish the 4 tabs | ~30 min + 1 rebuild on thor |
| **2 — robustness** | A3 (synthesized fallback, `missing_shapes` in result, `test_chord_shapes.py`, remaining A2 shapes) | small |
| **3 — solo rework** | B3 (per-measure fretboard boards: duration-sized dots + duration tail (default on), time-order badges, technique connectors with vector slide/vibrato icons, solo key panel, fret window + starting-fret label, beat ruler, dense-measure fallback, `solo_style` enum `board`\|`text`) + visual QA on all 4 songs | the big one |
| **4 — pipeline-side (Matrix)** | B4: pitch-contour note events so bends/vibrato become detectable; `tab_pdf.py` renders `b`/`v` marks when present | separate work item, GPU-host DSP |

### Open questions

- Technique thresholds: validate against SCOM's intro (known hammer-ons)
  and U2 One's verse (known slides) before tuning.
- Should `missing_shapes` also appear as a visible note line in the PDF
  header (not just the tool result)?