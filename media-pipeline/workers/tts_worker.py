#!/usr/bin/env python3
"""XTTS-v2 TTS worker (runs in comfyui container with venv-tts python).

Usage: tts_worker.py --text "..." [--voice trailer|default]
       [--reference-audio /path.wav] --out /path.wav

Voice model (XTTS-v2 is zero-shot cloning; every voice is a reference clip):
  - voice="trailer" (default): deep dramatic voice via zero-shot cloning of a
    pitch-shifted reference (cached in /basedir/models/tts/voices/trailer_ref.wav).
  - voice="default": the stock en_sample reference.
  - --reference-audio: use a user-supplied 3-10s reference clip instead.

Model files (pre-downloaded): /basedir/models/tts/XTTS-v2/{model.pth,config.json,
speakers_xtts.pth,vocab.json}. Loaded by directory (no download manager / no ToS prompt).
"""
import argparse
import os
import subprocess

MODEL_DIR = "/basedir/models/tts/XTTS-v2"
VOICES_DIR = "/basedir/models/tts/voices"
DEFAULT_REF = os.path.join(VOICES_DIR, "en_sample.wav")
TRAILER_REF = os.path.join(VOICES_DIR, "trailer_ref.wav")


def _load_model():
    from TTS.api import TTS
    # XTTS load_checkpoint takes a directory (checkpoint_dir) containing
    # model.pth, vocab.json, speakers_xtts.pth.
    return TTS(model_path=MODEL_DIR, config_path=f"{MODEL_DIR}/config.json",
               progress_bar=False).to("cuda")


def make_trailer_reference():
    """Deep, dramatic reference clip for zero-shot cloning (cached)."""
    if os.path.exists(TRAILER_REF):
        return TRAILER_REF
    print("Generating trailer reference voice...", flush=True)
    os.makedirs(VOICES_DIR, exist_ok=True)
    model = _load_model()
    raw = os.path.join(VOICES_DIR, "trailer_raw.wav")
    model.tts_to_file(
        text=("In a world where nothing is certain. One choice. Changes everything. "
              "Coming soon. To theaters everywhere."),
        speaker_wav=DEFAULT_REF, language="en", file_path=raw)
    # pitch down ~12% + slight reverb/lowpass for trailer feel
    subprocess.run([
        "ffmpeg", "-y", "-i", raw,
        "-filter:a", ("asetrate=22050*0.88,aresample=22050,"
                      "aecho=0.8:0.7:60:0.25,lowpass=f=9000,volume=1.2"),
        "-ac", "1", "-ar", "22050", TRAILER_REF,
    ], check=True, capture_output=True)
    return TRAILER_REF


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--voice", default="trailer", choices=["trailer", "default"])
    ap.add_argument("--reference-audio", default=None, help="custom 3-10s reference clip")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    model = _load_model()

    if a.reference_audio:
        ref = a.reference_audio
    elif a.voice == "trailer":
        ref = make_trailer_reference()
    else:
        ref = DEFAULT_REF

    model.tts_to_file(text=a.text, speaker_wav=ref, language="en", file_path=a.out)
    if not os.path.exists(a.out):
        raise SystemExit(f"output not created: {a.out}")
    print(f"WROTE {a.out}", flush=True)


if __name__ == "__main__":
    main()