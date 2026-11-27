#!/usr/bin/env python3
"""Generate the three 33x33x33 .cube LUTs in this directory.

Format (verified empirically against ffmpeg 6.1 `lut3d` / `parse_cube`):
  Line 1:  LUT_3D_SIZE 33
  Then 33^3 = 35937 lines, each "out_R out_G out_B" (6-decimal floats in
  [0,1]) for the input color (i/32, j/32, k/32).
  Line order (0-indexed line L):
      i = L % 33        -> input R  (fastest)
      j = (L // 33) % 33 -> input G
      k = L // 1089     -> input B  (slowest)

This is the Iridas/ffmpeg flat layout, NOT the 3-block (R/G/B section)
layout. An identity LUT in this layout is byte-exact through
`ffmpeg -vf lut3d` (see README for the evidence).

These are STARTING POINTS, not masters. Tune the per-LUT constants below
and re-run:  python3 gen_real.py
"""
import os

N = 33
HERE = os.path.dirname(os.path.abspath(__file__))


def clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def clamp_range(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def smoothstep(e0: float, e1: float, x: float) -> float:
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def s_curve(x: float, s: float) -> float:
    """Mild S-curve: darker shadows, lighter highlights; fixed point at 0.5."""
    return clamp(x - s * x * (x - 1.0) * (2.0 * x - 1.0))


# --------------------------------------------------------------------------
# 1. cinematic_teal_amber
#    Shadows -> teal (R down ~8%, B up ~6%); highlights -> amber (R up ~8%,
#    B down ~6%); mild S-curve contrast. The luminance crossover sits just
#    above mid-gray so midtones read slightly teal.
# --------------------------------------------------------------------------
def cinematic_teal_amber(r, g, b):
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    shadow_w = smoothstep(0.65, 0.15, lum)     # 1 in shadows, 0 above 0.65
    highlight_w = smoothstep(0.50, 0.90, lum)  # 0 below 0.50, 1 above 0.90
    r2 = r * (1.0 + 0.08 * (highlight_w - shadow_w))
    g2 = g
    b2 = b * (1.0 + 0.06 * (shadow_w - highlight_w))
    return (s_curve(r2, 0.30), s_curve(g2, 0.30), s_curve(b2, 0.30))


# --------------------------------------------------------------------------
# 2. warm_neutral
#    Gentle overall warm shift: R up ~4%, B down ~4%, G untouched, plus a
#    very mild contrast lift.
# --------------------------------------------------------------------------
def warm_neutral(r, g, b):
    r2 = r * 1.04
    g2 = g
    b2 = b * 0.96
    return (s_curve(r2, 0.15), s_curve(g2, 0.15), s_curve(b2, 0.15))


# --------------------------------------------------------------------------
# 3. cool_noir
#    ~35% desaturation, cool shift (R down ~5%, B up ~5%), higher contrast
#    (deeper blacks, slightly compressed highlights).
# --------------------------------------------------------------------------
def cool_noir(r, g, b):
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    # desaturate 35% (keep 65% of the original saturation)
    r2 = luma + (r - luma) * 0.65
    g2 = luma + (g - luma) * 0.65
    b2 = luma + (b - luma) * 0.65
    # cool shift
    r2 *= 0.95
    b2 *= 1.05
    # higher contrast: crush shadow floor (deeper blacks), cap at 0.96
    # (slightly compressed highlights), slope 1.15 for more contrast.
    r2 = clamp_range((r2 - 0.03) * 1.15, 0.0, 0.96)
    g2 = clamp_range((g2 - 0.03) * 1.15, 0.0, 0.96)
    b2 = clamp_range((b2 - 0.03) * 1.15, 0.0, 0.96)
    return (r2, g2, b2)


LUTS = {
    "cinematic_teal_amber": cinematic_teal_amber,
    "warm_neutral": warm_neutral,
    "cool_noir": cool_noir,
}


def write_flat(path: str, fn) -> None:
    with open(path, "w") as f:
        f.write(f"LUT_3D_SIZE {N}\n")
        n2 = N * N
        for L in range(N * N * N):
            i = L % N            # r index (fastest)
            j = (L // N) % N     # g index
            k = L // n2          # b index (slowest)
            or_, og, ob = fn(i / (N - 1), j / (N - 1), k / (N - 1))
            f.write(f"{clamp(or_):.6f} {clamp(og):.6f} {clamp(ob):.6f}\n")


if __name__ == "__main__":
    for name, fn in LUTS.items():
        p = os.path.join(HERE, f"{name}.cube")
        write_flat(p, fn)
        print(f"wrote {p}")
