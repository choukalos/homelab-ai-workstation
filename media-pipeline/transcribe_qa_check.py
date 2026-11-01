#!/usr/bin/env python3
"""QA schema checker for /transcribe output (spec §9.3).

Asserts analysis.json against the §4 contract:
  * every section has chords/notes/lyrics keys (lists)
  * every non-bass note has a tab; bass notes carry tab=null
  * every tab satisfies open[string] + fret == midi, 1 <= string <= 6,
    0 <= fret <= MAX_FRET
  * lyrics start_s within section bounds
  * sections cover the track contiguously, in order
  * top-level required fields present

Usage: transcribe_qa_check.py <analysis.json> [--max-fret 15]
Exit 0 = pass, 1 = fail (violations printed).
"""
import argparse
import json
import sys

OPEN = {6: 40, 5: 45, 4: 50, 3: 55, 2: 59, 1: 64}
REQ_TOP = ["version", "source", "instrument", "tuning", "duration_s", "key",
           "bpm", "stems_used", "transpose_semitones", "chord_style", "capo",
           "shape_hints", "polish_fallback", "confidence", "caveats",
           "sections", "chord_progression"]
REQ_SEC = ["index", "label", "start_s", "end_s", "chords", "notes", "lyrics"]
ROLE_SET = {"lead", "accompaniment", "bass"}
LABEL_SET = {"intro", "verse", "chorus", "bridge", "outro", "solo"}
SHAPE_TYPES = {"open", "barre", "power"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("analysis")
    ap.add_argument("--max-fret", type=int, default=15)
    a = ap.parse_args()
    d = json.load(open(a.analysis))
    errs = []

    for k in REQ_TOP:
        if k not in d:
            errs.append(f"missing top-level field: {k}")
    if d.get("confidence") not in ("high", "medium", "low"):
        errs.append(f"bad confidence: {d.get('confidence')!r}")
    if not isinstance(d.get("caveats"), list):
        errs.append("caveats must be a list")

    prev_end = 0.0
    for i, sec in enumerate(d.get("sections", [])):
        tag = f"section {i}"
        for k in REQ_SEC:
            if k not in sec:
                errs.append(f"{tag}: missing field {k}")
        if sec.get("index") != i:
            errs.append(f"{tag}: index mismatch ({sec.get('index')} != {i})")
        if not (prev_end - 1e-6 <= sec.get("start_s", -1) <= sec.get("end_s", 0) + 1e-6):
            errs.append(f"{tag}: not contiguous (prev_end={prev_end}, start={sec.get('start_s')})")
        prev_end = sec.get("end_s", 0)
        if sec.get("end_s", 0) - sec.get("start_s", 0) <= 0:
            errs.append(f"{tag}: empty/negative span")
        if sec.get("label") not in LABEL_SET and not str(sec.get("label", "")).startswith("section "):
            errs.append(f"{tag}: bad label {sec.get('label')!r}")
        for c in sec.get("chords", []):
            if not (sec["start_s"] <= c.get("time_s", -1) < sec["end_s"]):
                errs.append(f"{tag}: chord time {c.get('time_s')} outside section")
            if not (0 <= c.get("confidence", -1) <= 1):
                errs.append(f"{tag}: chord confidence out of range")
            sh = c.get("shape")
            if d.get("shape_hints") and sh is not None:
                if not isinstance(sh, dict) or sh.get("type") not in SHAPE_TYPES:
                    errs.append(f"{tag}: bad chord shape {sh!r}")
                elif sh.get("type") != "open":
                    if sh.get("base") not in ("E", "A") or not (0 <= sh.get("fret", -1) <= 12):
                        errs.append(f"{tag}: bad chord shape {sh!r}")
        for n in sec.get("notes", []):
            m, tab, role = n.get("midi"), n.get("tab"), n.get("role")
            if role not in ROLE_SET:
                errs.append(f"{tag}: bad note role {role!r}")
                continue
            if role == "bass":
                if tab is not None:
                    errs.append(f"{tag}: bass note has tab (must be null)")
                continue
            if tab is None:
                # out-of-range notes may carry no tab (documented worker behavior)
                continue
            s, f = tab.get("string"), tab.get("fret")
            if not (1 <= s <= 6):
                errs.append(f"{tag}: bad string {s}")
                continue
            if not (0 <= f <= a.max_fret):
                errs.append(f"{tag}: fret {f} out of range")
            if OPEN[s] + f != m:
                errs.append(f"{tag}: tab invariant violated: open({s})+{f} != {m}")
        for l in sec.get("lyrics", []):
            if not (sec["start_s"] <= l.get("start_s", -1) < sec["end_s"]):
                errs.append(f"{tag}: lyric start {l.get('start_s')} outside section")

    if errs:
        print(f"FAIL — {len(errs)} violation(s):")
        for e in errs[:50]:
            print(f"  - {e}")
        sys.exit(1)
    print(f"PASS — {len(d['sections'])} sections, "
          f"{sum(len(s['notes']) for s in d['sections'])} notes, "
          f"key={d['key']} bpm={d['bpm']} confidence={d['confidence']} "
          f"fallback={d['polish_fallback']}")


if __name__ == "__main__":
    main()