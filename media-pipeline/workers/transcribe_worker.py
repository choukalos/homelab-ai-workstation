#!/usr/bin/env python3
"""Song transcription worker: audio/video -> guitar tab + timestamped lyrics.

Runs inside the comfyui container (venv-transcribe python). Spec:
song_transcribe_matrix_handoff.md (2026-10-31).

Stages (sequential, one process per job):
  0 ingest    ffmpeg -> audio.wav (44.1 kHz stereo s16)
  1 global    librosa: key (Krumhansl-Schmuckler), BPM (beat tracking, 60-180 guard)
  2 notes     basic-pitch (ONNX): polyphonic events -> song.mid + notes.json
  3 chords    CQT chord-template matching (beat-aligned, anti-flap)
  4 sections  deterministic segmentation (onset/energy envelope)
  5 lyrics    demucs htdemucs -> vocals.wav (default; --stems false = full mix);
              faster-whisper on the stem (word ts, VAD, hallucination filter)
  6 mapping   deterministic guitar mapping (role + string/fret, standard tuning)
  7 polish    vLLM matrix-coder (validated; deterministic fallback on rejection)

Design principle (binding): tab DATA (string/fret per note, chords, sections,
note events) is computed deterministically from the audio. The LLM only labels
sections, cleans ASR lyrics, and writes confidence notes. The LLM never
produces or alters a tab line; its output is diff-checked and the deterministic
rendering ships on validation failure (polish_fallback=true).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

import numpy as np
import soundfile as sf

SR = 44100
HOP = 512
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# standard tuning: string number (6=low E .. 1=high E) -> open-string MIDI
OPEN = {6: 40, 5: 45, 4: 50, 3: 55, 2: 59, 1: 64}
STRING_ORDER = [1, 2, 3, 4, 5, 6]  # tab grid top -> bottom (e B G D A E)

# Krumhansl-Schmuckler key profiles
KS_MAJOR = np.array([0.63, 0.08, 0.27, 0.25, 0.14, 0.21, 0.18, 0.16, 0.23, 0.18, 0.16, 0.08])
KS_MINOR = np.array([0.63, 0.25, 0.09, 0.15, 0.19, 0.18, 0.16, 0.11, 0.14, 0.18, 0.15, 0.10])

# chord qualities: suffix -> (root offsets, tone weights; 7ths/9ths at 0.5)
CHORD_QUALITIES = {
    "":    ([0, 4, 7],      [1.0, 1.0, 1.0]),
    "m":   ([0, 3, 7],      [1.0, 1.0, 1.0]),
    "maj7": ([0, 4, 7, 11], [1.0, 1.0, 1.0, 0.5]),
    "m7":  ([0, 3, 7, 10],  [1.0, 1.0, 1.0, 0.5]),
    "7":   ([0, 4, 7, 10],  [1.0, 1.0, 1.0, 0.5]),
    "dim": ([0, 3, 6],      [1.0, 1.0, 1.0]),
    "aug": ([0, 4, 8],      [1.0, 1.0, 1.0]),
    "sus2": ([0, 2, 7],     [1.0, 1.0, 1.0]),
    "sus4": ([0, 5, 7],     [1.0, 1.0, 1.0]),
    "6":   ([0, 4, 7, 9],   [1.0, 1.0, 1.0, 0.5]),
}


def log(msg):
    print(f"[transcribe] {msg}", flush=True)


def die(msg):
    raise RuntimeError(msg)


# ------------------------------------------------------------------ stage 0
def stage_ingest(path, out_dir):
    src = path
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=codec_type", "-of", "csv=p=0", src],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or "audio" not in r.stdout:
        die("no decodable audio track")
    wav = os.path.join(out_dir, "audio.wav")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src,
                        "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", wav],
                       capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        die(f"ffmpeg ingest failed: {r.stderr[-500:]}")
    dur = float(sf.info(wav).duration)
    if dur < 10.0:
        die("audio too short")
    log(f"ingest: {dur:.1f}s audio.wav")
    return wav, dur


# ------------------------------------------------------------------ stage 1
def stage_bpm(y):
    """Beat tracking + half/double-time guard (60-180)."""
    import librosa
    tempo, beats = librosa.beat.beat_track(y=y, sr=SR)
    tempo = float(np.atleast_1d(tempo)[0])
    # beat_track returns beat FRAMES on the hop grid, not seconds. The
    # conversion is mandatory: with raw frames, stage_chords' `b >= dur`
    # break kills the loop on the first beat and every window clamps to the
    # final frame — chord detection comes out empty.
    beats = [float(t) for t in librosa.frames_to_time(
        np.atleast_1d(beats), sr=SR, hop_length=HOP)]
    guard_note = None
    if tempo < 60:
        tempo *= 2.0
        guard_note = "bpm doubled into 60-180 guard range"
    elif tempo > 180:
        tempo /= 2.0
        guard_note = "bpm halved into 60-180 guard range"
    if not (60 <= tempo <= 180):
        guard_note = f"bpm {tempo:.0f} outside 60-180 even after guard"
    log(f"bpm: {tempo:.1f} ({len(beats)} beats)")
    return {"bpm": round(tempo, 1), "beats": beats, "bpm_guard": guard_note}


def _chord_root_pc(symbol):
    """Parse the root pitch-class from a chord symbol like 'C#min7' -> 1."""
    i = 0
    while i < len(symbol) and symbol[i] in "CDEFGAB":
        i += 1
    root = symbol[:i]
    if i < len(symbol) and symbol[i] == "#":
        root += "#"
        i += 1
    return NOTE_NAMES.index(root) if root in NOTE_NAMES else None


def stage_key(cqt, bin_pc, chords, note_events):
    """Key: multi-signal tonic + mode.
    Tonic: (1) most common chord root when chords are plentiful, (2) the
    strongest bass-root pitch class when the dominant (a 5th above) is the
    runner-up bass note — musically confirmed, (3) Krumhansl-Schmuckler winner.
    K-S alone is fooled by a strong dominant chord on dense mixes.
    Mode: (1) tonic chord qualities, (2) the 3rd decision (major-3rd vs
    minor-3rd CQT energy), (3) K-S."""
    mag = np.abs(cqt)
    pc = np.zeros(12)
    for p in range(12):
        pc[p] = mag[bin_pc == p].sum()
    pc = pc / (pc.sum() + 1e-9)
    scores = {}
    for root in range(12):
        for minor in (False, True):
            prof = np.roll(KS_MINOR if minor else KS_MAJOR, root)
            prof = prof / (prof.std() + 1e-9)
            pc_n = (pc - pc.mean()) / (pc.std() + 1e-9)
            scores[(root, minor)] = float(np.corrcoef(pc_n, prof)[0, 1])
    kss = max(scores.items(), key=lambda kv: kv[1])

    # ---- tonic
    tonic_pc, method = None, None
    roots = {}
    for c in chords:
        r = _chord_root_pc(c["symbol"])
        if r is not None:
            roots[r] = roots.get(r, 0) + 1
    if len(chords) >= 10 and roots:
        top = max(roots.values())
        tied = [r for r, n in roots.items() if n == top]
        if len(tied) == 1:
            tonic_pc, method = tied[0], "chord-root tonic"
        else:
            # tie (typical I-V-vi-iii loop): K-S correlation breaks it
            tonic_pc = max(tied, key=lambda r: max(scores[(r, False)],
                                                   scores[(r, True)]))
            method = "chord-root tonic (K-S tiebreak)"
    else:
        # bass-root tonic: strongest low-register note event pitch class,
        # confirmed when the runner-up is a perfect 5th above (the dominant)
        bass = {}
        for e in note_events:
            if 24 <= e["midi"] <= 48:  # C1-C3 bass register
                bass[e["midi"] % 12] = bass.get(e["midi"] % 12, 0.0) \
                    + (e["end_s"] - e["start_s"]) * e["amplitude"]
        ranked = sorted(bass.items(), key=lambda kv: -kv[1])
        if len(ranked) >= 2:
            t1, t2 = ranked[0][0], ranked[1][0]
            if (t1 + 7) % 12 == t2:
                tonic_pc, method = t1, "bass-root tonic (dominant-confirmed)"
    if tonic_pc is None:
        tonic_pc, method = kss[0][0], "K-S tonic"

    # ---- mode
    maj = minc = 0
    for c in chords:
        if _chord_root_pc(c["symbol"]) == tonic_pc:
            if "min" in c["symbol"]:
                minc += 1
            else:
                maj += 1
    if maj + minc >= 3 and abs(maj - minc) >= 2:
        minor, mode_method = minc > maj, "chord-quality mode"
    else:
        maj3 = pc[(tonic_pc + 4) % 12]
        min3 = pc[(tonic_pc + 3) % 12]
        minor, mode_method = min3 > maj3, "3rd-decision mode"

    final = (tonic_pc, minor)
    key = f"{NOTE_NAMES[tonic_pc]} {'minor' if minor else 'major'}"
    log(f"key: {key} ({method} / {mode_method}, score={scores[final]:.3f}, "
        f"K-S top={NOTE_NAMES[kss[0][0]]}{' minor' if kss[0][1] else ' major'} {kss[1]:.3f})")
    return {"key": key, "key_corr": round(scores[final], 4), "key_method": f"{method}/{mode_method}"}


# ------------------------------------------------------------------ stage 2
def stage_notes(wav, out_dir):
    from basic_pitch.inference import predict
    t0 = time.time()
    _model_output, midi_data, note_events = predict(
        wav, onset_threshold=0.5, frame_threshold=0.3)
    # note_events: (start_s, end_s, midi, amplitude, pitch_bends)
    events = []
    for start, end, midi, amp, _bends in note_events:
        start, end, midi, amp = float(start), float(end), int(round(float(midi))), float(amp)
        if not (0 < end - start < 60) or not (0 <= midi <= 127):
            continue
        events.append({"midi": midi, "start_s": round(start, 3),
                       "end_s": round(end, 3),
                       "freq_hz": round(440.0 * 2 ** ((midi - 69) / 12.0), 1),
                       "amplitude": round(amp, 4)})
    events.sort(key=lambda e: (e["start_s"], e["midi"]))
    log(f"notes: {len(events)} events in {time.time()-t0:.0f}s")

    midi_data.write(os.path.join(out_dir, "song.mid"))
    with open(os.path.join(out_dir, "notes.json"), "w") as f:
        json.dump(events, f)
    return events


# ------------------------------------------------------------------ stage 3
def _chord_templates():
    """(root, suffix, pitch-class set with per-tone weights)."""
    tpls = []
    for root in range(12):
        for suffix, (tones, weights) in CHORD_QUALITIES.items():
            pcs = []
            for t, w in zip(tones, weights):
                pcs.append(((root + t) % 12, w))
            tpls.append((root, suffix, pcs))
    return tpls


def stage_chords(cqt, bin_pc, beats, dur):
    """Beat-aligned chord-template matching with anti-flap.

    One-beat window per beat. Scores are normalized (template energy /
    window energy), so they are comparable only *within* a window: the
    incumbent is re-scored in the current window, never compared against
    its score from the window it was committed in (a stale-window compare
    made changes impossible after a high-scoring first chord).

    A chord is *reliable* when it beats the best template of any OTHER root
    by >= GAP (same-root quality variants like Dmaj7 vs D7 are near-ties by
    construction — the 7th/9th is CQT-ambiguous — so they must not block a
    clear root). A change commits when the reliable new root beats the
    incumbent's own template in the same window by >= MARGIN for PERSIST
    consecutive beats. Unreliable windows keep the incumbent — this
    suppresses the one-beat flapping that CQT template matching produces on
    sparse/dense mixes."""
    templates = _chord_templates()
    tpl_root = {f"{NOTE_NAMES[r]}{s}": r for r, s, _ in templates}
    bin_pc_sets = {p: (bin_pc == p) for p in range(12)}
    n_frames = cqt.shape[1]
    mag = np.abs(cqt)
    GAP, MARGIN, PERSIST = 0.015, 0.03, 2

    changes = []  # (time_s, symbol, confidence)
    incumbent = None
    pending = None
    for i, b in enumerate(beats):
        if b >= dur:
            break
        f0 = int(b * SR / HOP)
        f1 = int(beats[i + 1] * SR / HOP) if i + 1 < len(beats) \
            else min(f0 + int(0.5 * SR / HOP), n_frames)
        f0, f1 = max(0, min(f0, n_frames - 1)), max(f0 + 1, min(f1, n_frames))
        if f1 <= f0:
            continue
        window = mag[:, f0:f1]
        total = float(window.sum())
        if total <= 1e-9:
            continue
        scores = {}
        for root, suffix, pcs in templates:
            score = 0.0
            for p, w in pcs:
                score += w * float(window[bin_pc_sets[p]].sum())
            scores[f"{NOTE_NAMES[root]}{suffix}"] = score
        best_sym = max(scores, key=scores.get)
        best_s = scores[best_sym]
        best_other = max((s for sym, s in scores.items()
                          if tpl_root[sym] != tpl_root[best_sym]), default=0.0)
        reliable = (best_s - best_other) / total >= GAP
        if incumbent is None:
            incumbent = best_sym
            changes.append((round(b, 3), best_sym, round(best_s / total, 4)))
            pending = None
        elif tpl_root[incumbent] == tpl_root[best_sym]:
            pending = None  # same root: keep incumbent symbol (no quality flap)
        elif reliable and (best_s - scores[incumbent]) / total >= MARGIN:
            if pending == best_sym:
                # PERSIST consecutive reliable beats favor the new root
                incumbent = best_sym
                changes.append((round(b, 3), best_sym, round(best_s / total, 4)))
                pending = None
            else:
                pending = best_sym
        else:
            pending = None
    # drop a final chord shorter than 0.8 s (edge artifact)
    if len(changes) > 1 and dur - changes[-1][0] < 0.8:
        changes.pop()
    log(f"chords: {len(changes)} changes")
    return [{"time_s": t, "symbol": s, "confidence": c} for t, s, c in changes]


# ------------------------------------------------------------------ stage 4
def stage_sections(y, dur, min_section_s, chords=None):
    import librosa
    onset_env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    t = librosa.frames_to_time(np.arange(len(rms)), sr=SR, hop_length=HOP)

    def smooth(x, win_s):
        w = max(1, int(win_s * SR / HOP))
        k = np.ones(w) / w
        return np.convolve(x, k, mode="same")

    def finalize(cands, source):
        cands = [c for c in cands if 1.0 < c < dur - 1.0]
        merged = []
        for c in sorted(cands):
            if not merged or c - merged[-1] >= min_section_s:
                merged.append(c)
        bounds = [0.0] + merged + [dur]
        secs = []
        for a, b in zip(bounds[:-1], bounds[1:]):
            if secs and b - a < 2.0:
                secs[-1][1] = b
            elif secs and secs[-1][1] - secs[-1][0] < 2.0:
                secs[-1][1] = b
            else:
                secs.append([a, b])
        sections = [{"index": i, "start_s": round(a, 3), "end_s": round(b, 3)}
                    for i, (a, b) in enumerate(secs)]
        log(f"sections: {len(sections)} ({source})")
        return sections

    onset_sm = smooth(onset_env, 0.05)
    local_mean = smooth(onset_sm, 2.0)
    quiet = onset_sm < 0.2 * (local_mean + 1e-9)

    # candidate boundaries: local rms minima inside quiet runs >= 0.5 s
    candidates = []
    i, n = 0, len(quiet)
    while i < n:
        if quiet[i]:
            j = i
            while j + 1 < n and quiet[j + 1]:
                j += 1
            if t[j] - t[i] >= 0.5:
                seg = np.arange(i, j + 1)
                k = seg[int(np.argmin(rms[seg]))]
                candidates.append(float(t[k]))
            i = j + 1
        else:
            i += 1
    sections = finalize(candidates, "quiet-runs")

    # fallback 1: chord-change boundaries (sections change with the harmony)
    if len(sections) < 3 and chords:
        cc = [c["time_s"] for c in chords if c["confidence"] >= 0.30]
        sections = finalize(candidates + cc, "quiet-runs + chord-changes")

    # fallback 2: fixed division (~20 s sections) — continuous music with no
    # quiet runs and no reliable chords
    if len(sections) < 3:
        n_sec = max(2, int(round(dur / 20.0)))
        step = dur / n_sec
        sections = finalize([round(i * step, 3) for i in range(1, n_sec)],
                            f"fixed {n_sec}-way division")
    return sections


# ------------------------------------------------------------------ stage 5
# ASR on the FULL MIX is the main failure mode for song lyrics. The Silero
# VAD inside faster-whisper is a *speech* detector: on a full mix it
# classifies vocal-dominated passages as "speech" and guitar-dominated
# passages as "music". Measured on Riptide (2026-10-31): the VAD kept only
# 7% of the full mix (the quiet, vocal-dominated verse) and dropped ~140 s
# of the guitar-heavy chorus, while on the demucs vocal stem it kept 64% in
# clean contiguous regions aligned to the sung sections. So the default is
# demucs vocals -> VAD+ASR on the stem (4 garbled lines -> 55 clean lines
# on the same track). no_speech_prob is deliberately NOT used as a filter:
# sung vocals score 0.6-0.9 even on genuine lines, so filtering on it would
# drop the verses.
_COMP_MAX, _LOGP_MIN = 2.4, -1.0


def _norm_text(s):
    return re.sub(r"[\W_]+", " ", s.lower()).strip()


def stage_lyrics(wav, out_dir, args):
    """Returns (lines, stems_used, caveat|None)."""
    caveat = None
    vocals = None
    if args.stems:
        log(f"stems: demucs {args.demucs_model} ...")
        r = subprocess.run([sys.executable, "-m", "demucs", "-n", args.demucs_model,
                            "--two-stems", "vocals",
                            "-o", os.path.join(out_dir, "demucs"), wav],
                           capture_output=True, text=True, timeout=3600)
        vocals = os.path.join(out_dir, "demucs", args.demucs_model, "audio", "vocals.wav")
        if r.returncode != 0 or not os.path.exists(vocals):
            # lyrics are the valuable output — never fail the job over stems:
            # fall back to full-mix ASR (degraded on dense mixes) + caveat
            caveat = ("demucs failed — ASR ran on the full mix; expect garbled "
                      "lines where the music dominates the vocals")
            log(f"stems: demucs failed ({r.stderr[-200:]}) — falling back to full mix")
            vocals = None
    asr_path = vocals if vocals else wav
    log(f"asr: faster-whisper {args.whisper_model} ({args.whisper_device}) on "
        f"{'vocals stem' if vocals else 'full mix'} ...")
    from faster_whisper import WhisperModel
    t0 = time.time()
    try:
        model = WhisperModel(args.whisper_model, device=args.whisper_device,
                             compute_type="float16" if args.whisper_device == "cuda" else "int8")
    except Exception as e:
        die(f"whisper model {args.whisper_model!r} unavailable: {e}")
    segments, _info = model.transcribe(asr_path, word_timestamps=True,
                                       vad_filter=True, beam_size=5)
    lines = []
    n_dropped = 0
    for seg in segments:
        # hallucination backstop: instrumental sections misheard as words show
        # up as high compression and/or collapsed logprob (standard whisper
        # guards; sung lines measured <= 2.33 / >= -0.66 on the stem)
        if seg.compression_ratio > _COMP_MAX or seg.avg_logprob < _LOGP_MIN:
            n_dropped += 1
            continue
        words = [{"start_s": round(w.start, 3), "end_s": round(w.end, 3),
                  "word": w.word} for w in (seg.words or [])]
        text = seg.text.strip()
        if text:
            lines.append({"start_s": round(seg.start, 3), "end_s": round(seg.end, 3),
                          "text": text, "words": words})
    # merge consecutive identical lines (whisper repeats lines over
    # instrumental breaks) into the first occurrence
    deduped = []
    for ln in lines:
        if deduped and _norm_text(ln["text"]) == _norm_text(deduped[-1]["text"]) \
                and ln["start_s"] - deduped[-1]["end_s"] <= 0.5:
            deduped[-1]["end_s"] = max(deduped[-1]["end_s"], ln["end_s"])
            if ln.get("words"):
                deduped[-1].setdefault("words", []).extend(ln["words"])
        else:
            deduped.append(ln)
    if len(deduped) != len(lines):
        log(f"asr: merged {len(lines)-len(deduped)} duplicate line(s)")
    if n_dropped:
        log(f"asr: dropped {n_dropped} hallucinated segment(s) "
            f"(compression>{_COMP_MAX} or logprob<{_LOGP_MIN})")
    lines = deduped
    log(f"asr: {len(lines)} lines in {time.time()-t0:.0f}s")
    return lines, bool(vocals), caveat


def write_lyrics_txt(lines, out_dir, disabled=False, name="lyrics.txt"):
    with open(os.path.join(out_dir, name), "w") as f:
        if disabled:
            f.write("# lyrics disabled\n")
            return
        for ln in lines:
            m, s = divmod(ln["start_s"], 60)
            f.write(f"[{int(m):02d}:{s:04.1f}] {ln['text']}\n")


# ------------------------------------------------------------------ stage 6
def stage_mapping(events, sections, max_fret):
    """Role classification + string/fret assignment (deterministic)."""
    by_start = sorted(events, key=lambda e: e["start_s"])
    for ev in by_start:
        ev["role"] = None
    for i, ev in enumerate(by_start):
        m = ev["midi"]
        cluster = any(abs(o["start_s"] - ev["start_s"]) <= 0.05 for o in by_start if o is not ev)
        if m < 45:
            ev["role"] = "bass"
        elif (not cluster) and 45 <= m <= 84:
            ev["role"] = "lead"
        else:
            ev["role"] = "accompaniment"

    # refinement: monophonic melodic runs of accompaniment -> lead
    i = 0
    while i < len(by_start):
        ev = by_start[i]
        if ev["role"] != "accompaniment" or ev["midi"] < 45:
            i += 1
            continue
        run = [ev]
        j = i + 1
        while j < len(by_start) and by_start[j]["role"] == "accompaniment" \
                and abs(by_start[j]["start_s"] - run[-1]["end_s"]) < 0.15 \
                and by_start[j]["midi"] >= 45:
            run.append(by_start[j])
            j += 1
        if len(run) >= 4:
            dirs = [np.sign(run[k + 1]["midi"] - run[k]["midi"]) for k in range(len(run) - 1)]
            if len(set(dirs)) >= 2:
                for ev in run:
                    ev["role"] = "lead"
        i = j if j > i + 1 else i + 1

    # string/fret scoring
    open_list = [OPEN[s] for s in STRING_ORDER]
    last_fret = {}
    last_time = {}
    for ev in by_start:
        if ev["role"] == "bass":
            ev["tab"] = None
            continue
        m = ev["midi"]
        cands = []
        for s in STRING_ORDER:
            fret = m - OPEN[s]
            if 0 <= fret <= max_fret:
                cands.append((s, fret))
        if not cands:
            ev["tab"] = None  # out of range: keep the note, no tab
            continue
        best = None
        for s, fret in cands:
            pref = min(STRING_ORDER, key=lambda ss: abs(OPEN[ss] - m))
            score = (fret
                     + 2 * abs(pref - s)
                     + (3 * abs(fret - last_fret[s]) if s in last_fret
                        and ev["start_s"] - last_time.get(s, 0) <= 1.0 else 0)
                     + (100 if fret > 12 else 0))
            key = (score, s)
            if best is None or key < best[0]:
                best = (key, (s, fret))
        s, fret = best[1]
        assert OPEN[s] + fret == m, f"tab invariant violated: {OPEN[s]}+{fret}!={m}"
        ev["tab"] = {"string": s, "fret": fret}
        last_fret[s] = fret
        last_time[s] = ev["start_s"]
    n_lead = sum(1 for e in by_start if e["role"] == "lead")
    log(f"mapping: {n_lead} lead / "
        f"{sum(1 for e in by_start if e['role'] == 'accompaniment')} accomp / "
        f"{sum(1 for e in by_start if e['role'] == 'bass')} bass")
    return by_start


# ------------------------------------------------------------------ stage 7
def _line_similarity(a, b):
    """Case/punctuation-insensitive character similarity between a polished
    line and the raw line it cleans (1.0 = identical, 0.0 = unrelated)."""
    import difflib
    a = re.sub(r"[^a-z0-9 ]+", " ", a.lower()).strip()
    b = re.sub(r"[^a-z0-9 ]+", " ", b.lower()).strip()
    return difflib.SequenceMatcher(None, a, b).ratio()


def _strip_accents(s):
    return re.sub(r"[\u0300-\u036f]", "", s)


def _words_of(text):
    return {_strip_accents(w) for w in re.findall(r"[a-z0-9']+", _strip_accents(text).lower())}


def _llm_polish(analysis, sections, raw_lines, vllm_url, vllm_model):
    """Single matrix-coder call (thinking OFF). Returns (polish, usage) or
    (None, None) on any failure — the caller ships the deterministic fallback."""
    compact = {"title": None, "key": analysis["key"], "bpm": analysis["bpm"],
               "sections": []}
    for sec in sections:
        lead = [e for e in sec["notes"] if e["role"] == "lead"][:24]
        compact["sections"].append({
            "index": sec["index"], "start_s": sec["start_s"], "end_s": sec["end_s"],
            "chords": [c["symbol"] for c in sec["chords"]],
            "lead_preview": " ".join(NOTE_NAMES[e["midi"] % 12] + str(e["midi"] // 12 - 1)
                                     for e in lead),
            "raw_lyrics": [{"start_s": l["start_s"], "text": l["text"]}
                            for l in sec["lyrics"]],
        })
    sys = (
        "You are a music transcription assistant. Given the deterministic analysis "
        "of a song (key, bpm, per-section chords, condensed lead notes, raw ASR "
        "lyrics with timestamps), return STRICT JSON only: {\"title\": str|null, \"artist\": str|null, "
        "\"sections\": [{\"index\": int, \"label\": \"intro|verse|chorus|bridge|outro|"
        "solo\", \"lyrics\": [{\"start_s\": float, \"end_s\": float, \"text\": str}], "
        "\"note\": str|null}], \"confidence\": \"high|medium|low\", \"caveats\": [str]}. "
        "Rules: sections must keep the exact count and order of the input (same "
        "index values). lyrics must be a CLEANING of the raw_lyrics lines for that "
        "section: each output line keeps the start_s of the raw line it cleans "
        "(within 1.0 s) and stays faithful to that line — fix ASR mishearings "
        "(e.g. 'sistone' -> 'stoning'), punctuation and capitalization; never "
        "invent lines or content that is not in the raw line. Label sections using chord-loop similarity and lyrics "
        "presence. confidence reflects how reliable the tab data looks (dense "
        "mixes = lower). No text outside the JSON."
    )
    body = json.dumps({
        "model": vllm_model,
        "messages": [{"role": "system", "content": sys},
                     {"role": "user", "content": json.dumps(compact)}],
        "temperature": 0.2, "max_tokens": 16000,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(vllm_url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read())
    text = resp["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        text = text[4:] if text.startswith("json") else text
        text = text.strip()
    data = json.loads(text)

    # ---- validation (reject the WHOLE output on any violation)
    def reject(reason):
        log(f"polish: LLM output rejected — {reason}")
        return None, None

    if not isinstance(data.get("sections"), list) or \
            len(data["sections"]) != len(sections):
        return reject(f"sections count {len(data.get('sections') or [])} != input {len(sections)}")
    by_idx = {}
    for s in data["sections"]:
        if not isinstance(s, dict):
            return reject(f"section not a dict: {s!r}")
        idx = s.get("index")
        if idx in by_idx:
            return reject(f"duplicate section index {idx}")
        by_idx[idx] = s
    for sec in sections:
        ds = by_idx.get(sec["index"])
        if ds is None:
            return reject(f"section {sec['index']} missing from LLM output")
        if ds.get("label") not in ("intro", "verse", "chorus", "bridge", "outro", "solo", None):
            return reject(f"section {sec['index']}: bad label {ds.get('label')!r}")
        for line in (ds.get("lyrics") or []):
            if not isinstance(line.get("start_s"), (int, float)):
                return reject(f"section {sec['index']}: lyric line without numeric start_s")
            if not sec["lyrics"]:
                return reject(f"section {sec['index']}: polished line but no raw lines")
            # nearest raw line wins — raw lines can be <1.0 s apart ("Oh" /
            # "Lady" interjections), so a first-match-in-window matcher
            # mispairs them and rejects otherwise-clean output
            match = min(sec["lyrics"], key=lambda l: abs(l["start_s"] - line["start_s"]))
            if abs(match["start_s"] - line["start_s"]) > 1.0:
                return reject(f"section {sec['index']}: lyric start_s {line['start_s']} matches no raw line within 1.0 s")
            # the polished line must be a faithful cleaning of its raw line:
            # ASR fixes (word substitutions) allowed, wholesale invention not.
            # (A strict word-subset check would forbid fixing ANY mishearing —
            # 'sistone' -> 'stoning' is a "new word" — so use text similarity.)
            if _line_similarity(line.get("text", ""), match["text"]) < 0.5:
                return reject(f"section {sec['index']}: polished line diverges from its raw line")
    if data.get("confidence") not in ("high", "medium", "low"):
        return reject(f"bad confidence {data.get('confidence')!r}")
    if not isinstance(data.get("caveats"), list) or \
            not all(isinstance(c, str) for c in data["caveats"]):
        return reject("caveats not a list of strings")
    return data, resp.get("usage")


def apply_polish(analysis, sections, polish):
    if polish is None:
        analysis["polish_fallback"] = True
        for sec in sections:
            sec["label"] = f"section {sec['index']}"
        if "LLM polish unavailable — raw output" not in analysis["caveats"]:
            analysis["caveats"].append("LLM polish unavailable — raw output")
        return
    analysis["polish_fallback"] = False
    analysis["title"] = polish.get("title")
    analysis["artist"] = polish.get("artist")
    # the LLM may only LOWER confidence (it sees the same data we do; it can't
    # know the ground truth), never raise it above the DSP heuristic
    _rank = {"low": 0, "medium": 1, "high": 2}
    llm_conf = polish.get("confidence")
    if llm_conf in _rank and _rank[llm_conf] < _rank.get(analysis["confidence"], 1):
        analysis["confidence"] = llm_conf
    analysis["caveats"] = list(polish.get("caveats") or []) + analysis["caveats"]
    by_idx = {s["index"]: s for s in polish["sections"]}
    for sec in sections:
        ds = by_idx[sec["index"]]
        sec["label"] = ds.get("label") or f"section {sec['index']}"
        if ds.get("note"):
            sec["note"] = ds["note"]
        if ds.get("lyrics"):
            # keep the word-level timestamps: each polished line maps to the
            # raw line it cleans (start_s within 1.0 s) — copy that raw line's
            # words array so the tab's word timestamps survive the polish
            new_lines = []
            for l in ds["lyrics"]:
                raw = (min(sec["lyrics"], key=lambda r: abs(r["start_s"] - l["start_s"]))
                       if sec["lyrics"] else None)
                if raw is not None and abs(raw["start_s"] - l["start_s"]) > 1.0:
                    raw = None
                nl = {"start_s": l["start_s"],
                      "end_s": l.get("end_s", l["start_s"]),
                      "text": l["text"]}
                if raw and raw.get("words"):
                    nl["words"] = raw["words"]
                new_lines.append(nl)
            sec["lyrics"] = new_lines


# ------------------------------------------------------------------ tab.md
def _fmt_ts(t):
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:04.1f}"


def render_tab_md(analysis, sections, full_notes):
    lines = []
    title = analysis.get("title") or "Untitled"
    artist = analysis.get("artist") or "unknown"
    lines.append(f"# {title} — guitar tab\n")
    lines.append(f"Artist: {artist} | Key: {analysis['key']} | BPM: {analysis['bpm']} | "
                 f"Tuning: Standard (EADGBE)")
    lines.append(f"Source: {analysis['source']}")
    lines.append(f"Confidence: {analysis['confidence']}"
                 + (f" — {'; '.join(analysis['caveats'])}" if analysis["caveats"] else ""))
    lines.append("Raw data: analysis.json / song.mid / lyrics_raw.txt (verify lines against these).\n")
    sixteenth = 60.0 / analysis["bpm"] / 4.0
    for sec in sections:
        a, b = sec["start_s"], sec["end_s"]
        lines.append(f"## [{sec['label']}] ({_fmt_ts(a)}–{_fmt_ts(b)})\n")
        if sec["chords"]:
            lines.append("Chords: " + " | ".join(c["symbol"] for c in sec["chords"]))
        # 16th-note grid, one block per 16 sixteenths (a 4/4 bar)
        cols_total = max(1, int(round((b - a) / sixteenth)))
        chord_at = {}
        for c in sec["chords"]:
            col = int(round((c["time_s"] - a) / sixteenth))
            if col >= 0:
                chord_at[col] = c["symbol"]
        notes = [e for e in sec["notes"] if e["role"] in ("lead",) or full_notes]
        for block_start in range(0, cols_total, 16):
            grid = {s: ["-"] * 16 for s in STRING_ORDER}
            chord_row = [" "] * 16
            for col in range(16):
                gcol = block_start + col
                if gcol in chord_at:
                    chord_row[col] = chord_at[gcol]
            for ev in notes:
                if ev.get("tab") is None:
                    continue
                c0 = int(round((ev["start_s"] - a) / sixteenth))
                c1 = int(round((ev["end_s"] - a) / sixteenth))
                if c1 < c0:
                    c1 = c0
                for col in range(c0, min(c1 + 1, cols_total)):
                    if block_start <= col < block_start + 16:
                        grid[ev["tab"]["string"]][col - block_start] = str(ev["tab"]["fret"])
            lines.append("")
            lines.append("   " + "".join(chord_row))
            for s in STRING_ORDER:
                lines.append(f"   {['e','B','G','D','A','E'][STRING_ORDER.index(s)]}|"
                             + "".join(grid[s]) + "|")
        if sec["lyrics"]:
            for l in sec["lyrics"]:
                lines.append(f"\nLyrics ({_fmt_ts(l['start_s'])}): {l['text']}")
        else:
            lines.append("\nLyrics: (none — instrumental)")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True, help="source media file (container path)")
    ap.add_argument("--out-dir", required=True, help="job dir (container path)")
    ap.add_argument("--source-display", default=None, help="path as given in the job payload")
    ap.add_argument("--instrument", default="guitar")
    ap.add_argument("--tuning", default="standard")
    ap.add_argument("--lyrics", default="true")
    ap.add_argument("--stems", default="true")
    ap.add_argument("--whisper-model", default=None)
    ap.add_argument("--whisper-device", default=None)
    ap.add_argument("--demucs-model", default=None)
    ap.add_argument("--max-fret", type=int, default=None)
    ap.add_argument("--min-section-s", type=float, default=None)
    ap.add_argument("--full-notes", default="false")
    ap.add_argument("--vllm-url", default=None)
    ap.add_argument("--vllm-model", default=None)
    a = ap.parse_args()

    out_dir = a.out_dir
    os.makedirs(out_dir, exist_ok=True)
    max_fret = a.max_fret if a.max_fret is not None else int(os.environ.get("MEDIA_TRANSCRIBE_MAX_FRET", "15"))
    min_section_s = a.min_section_s if a.min_section_s is not None else float(os.environ.get("MEDIA_TRANSCRIBE_MIN_SECTION_S", "4"))
    whisper_model = a.whisper_model or os.environ.get("MEDIA_TRANSCRIBE_WHISPER_MODEL", "large-v3")
    whisper_device = a.whisper_device or os.environ.get("MEDIA_TRANSCRIBE_WHISPER_DEVICE", "cuda")
    demucs_model = a.demucs_model or os.environ.get("MEDIA_TRANSCRIBE_DEMUCS_MODEL", "htdemucs")
    vllm_url = a.vllm_url or os.environ.get("MEDIA_TRANSCRIBE_VLLM_URL", "http://192.168.4.55:8000/v1/chat/completions")
    vllm_model = a.vllm_model or os.environ.get("MEDIA_TRANSCRIBE_VLLM_MODEL", "qwen38-27b")
    source_display = a.source_display or a.path
    # one-time model downloads land in the shared (host-persistent) HF cache dir
    os.environ.setdefault("HF_HOME", "/comfy/mnt/HF")
    do_lyrics = a.lyrics.strip().lower() in ("1", "true", "yes")
    do_stems = a.stems.strip().lower() in ("1", "true", "yes")
    full_notes = a.full_notes.strip().lower() in ("1", "true", "yes")

    t0 = time.time()
    # 0. ingest
    wav, dur = stage_ingest(a.path, out_dir)
    y, _ = sf.read(wav, dtype="float32")
    if y.ndim > 1:
        y = y.mean(axis=1)

    import librosa
    # shared CQT for key + chords: 144 bins from C2 (65.4 Hz), 36/oct
    # -> 65.4-1046 Hz (bass + guitar mid register). Each bin is 1/3 of a
    # semitone, so bin i -> MIDI = 36 + i/3.
    fmin = 65.4
    n_bins = 144
    cqt = np.abs(librosa.cqt(y, sr=SR, hop_length=HOP, n_bins=n_bins,
                             bins_per_octave=36, fmin=fmin))
    bin_pc = np.round(36.0 + np.arange(n_bins) * 12.0 / 36.0).astype(int) % 12

    # 2. notes (basic-pitch) — computed before the key so chord roots are
    # available for the tonic tiebreak
    events = stage_notes(wav, out_dir)

    # 1. bpm (beat grid for chords)
    g_bpm = stage_bpm(y)

    # 3. chords
    chords = stage_chords(cqt, bin_pc, g_bpm["beats"], dur)

    # 1. key (chord-root tonic + K-S fallback)
    g = stage_key(cqt, bin_pc, chords, events)
    g.update(g_bpm)

    # 4. sections (quiet-runs, chord-change fallback, fixed division)
    sections = stage_sections(y, dur, min_section_s, chords)

    # 5. lyrics (vocal stem by default — see stage 5 comment)
    raw_lines, stems_used, asr_caveat = ([], False, None)
    if do_lyrics:
        raw_lines, stems_used, asr_caveat = stage_lyrics(wav, out_dir, argparse.Namespace(
            stems=do_stems, whisper_model=whisper_model, whisper_device=whisper_device,
            demucs_model=demucs_model))
        # raw ASR output is kept separately (reproducible); lyrics.txt is
        # rewritten with the final (polished) lines after stage 7
        write_lyrics_txt(raw_lines, out_dir, name="lyrics_raw.txt")
    else:
        write_lyrics_txt([], out_dir, disabled=True)

    # whisper hallucination guard (spec §10): a section with >10 s without
    # onsets is instrumental — suppress its lines (known whisper failure mode)
    if raw_lines:
        onset_env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
        t_env = librosa.frames_to_time(np.arange(len(onset_env)), sr=SR, hop_length=HOP)
        thr = 0.01 * float(onset_env.mean())
        for sec in sections:
            if sec["end_s"] - sec["start_s"] <= 10.0:
                continue
            m0 = np.searchsorted(t_env, sec["start_s"])
            m1 = np.searchsorted(t_env, sec["end_s"])
            quiet = onset_env[m0:m1] < thr
            longest, run = 0.0, 0.0
            for q in quiet:
                run = (run + HOP / SR) if q else 0.0
                longest = max(longest, run)
            if longest > 10.0:
                sec["lyrics_suppressed"] = True
                log(f"sections: {sec['index']} has >10s without onsets — suppressing lyrics")

    # 6. mapping
    mapped = stage_mapping(events, sections, max_fret)
    for sec in sections:
        sec["notes"] = [e for e in mapped if sec["start_s"] <= e["start_s"] < sec["end_s"]]
        sec["chords"] = [c for c in chords if sec["start_s"] <= c["time_s"] < sec["end_s"]]
        sec["lyrics"] = [l for l in raw_lines if sec["start_s"] <= l["start_s"] < sec["end_s"]] if not sec.pop("lyrics_suppressed", False) else []

    # chord progression: the loop with the most chord events (spec §4) —
    # the section with the most chords, its sequence, consecutive repeats dropped
    prog = "n/a"
    if chords:
        busiest = max(sections, key=lambda s: len(s["chords"]))
        seq = []
        for c in busiest["chords"]:
            if not seq or seq[-1] != c["symbol"]:
                seq.append(c["symbol"])
        prog = " - ".join(seq) if seq else "n/a"

    # DSP confidence heuristic (fallback baseline). Smear (dense polyphony)
    # caps confidence; a musically-confirmed tonic (chord-root or
    # dominant-confirmed bass-root) earns high, K-S-only earns at most medium.
    lead_frac = (sum(1 for e in mapped if e["role"] == "lead") / max(1, len(mapped)))
    clusters = 0
    if mapped:
        clusters = sum(1 for e in mapped
                       if any(abs(o["start_s"] - e["start_s"]) <= 0.05 for o in mapped if o is not e))
    smear = bool(mapped) and clusters / len(mapped) > 0.30
    key_method = g.get("key_method", "")
    if smear:
        confidence = "low"
    elif "chord-root" in key_method or "dominant-confirmed" in key_method:
        confidence = "high" if lead_frac > 0.15 else "medium"
    else:
        confidence = "medium" if lead_frac > 0.15 else "low"

    analysis = {
        "version": 1,
        "source": source_display,
        "instrument": a.instrument,
        "tuning": a.tuning,
        "duration_s": round(dur, 3),
        "key": g["key"],
        "bpm": g["bpm"],
        "stems_used": stems_used,
        "polish_fallback": False,
        "confidence": confidence,
        "caveats": [],
        "sections": sections,
        "chord_progression": prog,
    }
    if g["bpm_guard"]:
        analysis["caveats"].append(g["bpm_guard"])
    if asr_caveat:
        analysis["caveats"].append(asr_caveat)
    # heavy-distortion smear caveat
    if smear:
        analysis["caveats"].append(
                "dense polyphony — basic-pitch may smear on distortion/pitch-shift")

    # 7. polish (LLM; never blocks the job)
    try:
        polish, usage = _llm_polish(analysis, sections, raw_lines, vllm_url, vllm_model)
    except Exception as e:
        log(f"polish: LLM call failed ({e}); using deterministic fallback")
        polish, usage = None, None
    apply_polish(analysis, sections, polish)
    if polish is not None:
        log(f"polish: accepted (usage={usage})")
    else:
        log("polish: fallback (deterministic output)")
    # final user-facing lyrics (polished when the LLM step was accepted,
    # deterministic raw lines otherwise)
    if do_lyrics:
        write_lyrics_txt([l for sec in sections for l in sec["lyrics"]], out_dir)

    with open(os.path.join(out_dir, "analysis.json"), "w") as f:
        json.dump(analysis, f, indent=2)
    with open(os.path.join(out_dir, "tab.md"), "w") as f:
        f.write(render_tab_md(analysis, sections, full_notes))

    log(f"DONE in {time.time()-t0:.0f}s")
    print(json.dumps({"ok": True, "duration_s": analysis["duration_s"],
                      "key": analysis["key"], "bpm": analysis["bpm"],
                      "n_sections": len(sections), "n_notes": len(mapped),
                      "polish_fallback": analysis["polish_fallback"]}))


if __name__ == "__main__":
    main()