# 3D LUTs (starting points)

Three 33×33×33 `.cube` LUTs for the media-pipeline. **These are starting
points, not masters** — tune the per-LUT constants in `gen_real.py` (or edit
the curves directly) to taste before treating any of them as a final grade.

## Format (verified against ffmpeg `lut3d`)

Each file is the Iridas/ffmpeg `parse_cube` layout, **not** the 3-block
(R/G/B section) layout:

- Line 1: `LUT_3D_SIZE 33`
- Then `33³ = 35937` lines, each `out_R out_G out_B` (6-decimal floats in
  `[0,1]`) for the input at `(i/32, j/32, k/32)`.
- Line order: `i = L % 33` (input **R**, fastest), `j = (L//33) % 33` (input
  **G**), `k = L // 1089` (input **B**, slowest).

This layout was confirmed empirically (see `media_jobs/lut_test` evidence):
an identity LUT in this layout is byte-exact through `ffmpeg -vf lut3d`
(≤1/255 rounding on 8-bit grid points), while the 3-section and letter-prefixed
variants fail to parse (`rc=183`). ffmpeg 6.1, CPU only.

## Usage

Pass a file to the media-pipeline `/assemble` endpoint via the `lut` param;
it is applied to the final encode with ffmpeg's `lut3d` filter (CPU):

```
POST /assemble  { "shots": [...], "lut": "luts/cinematic_teal_amber.cube", ... }
```

Equivalent standalone ffmpeg: `-vf "lut3d=/path/to/lut.cube"`.

## The LUTs

- **`cinematic_teal_amber.cube`** — the classic teal-and-orange look. A mild
  S-curve adds contrast, then luminance-weighted shifts push shadows toward
  teal (R down, G/B up) and highlights toward amber (R up, B down). Use it as
  the default "film" grade for narrative/dramatic footage. *Starting point.*

- **`warm_neutral.cube`** — a gentle, near-neutral warm lift: R up ~4% of the
  full range, B down ~4%, G untouched, plus a very mild contrast lift. Safe
  for skin tones and "make it a touch warmer" passes without a strong style.
  *Starting point.*

- **`cool_noir.cube`** — a moody desaturated cool grade: ~35% desaturation, a
  cool shift (R down ~5%, B up ~5%), and a stronger S-curve for deeper blacks
  and slightly clipped highlights. Use for tense/noir/cinematic-black moods.
  *Starting point.*
