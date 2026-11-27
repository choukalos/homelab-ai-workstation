"""Film project store — the "project bible" (films plan B1).

One dir per film under RUN_DIR/media_projects (env MEDIA_PROJECTS_ROOT),
mirrored append-only to the NAS (env MEDIA_NAS_ROOT, default
/mnt/lego/media_projects). Local is the source of truth; the NAS copy is
permanent (rsync --ignore-existing, never --delete — deletions on Matrix do
NOT propagate).

Single writer: every mutation goes through the service endpoints (see
server.py routes /projects/...). This module owns the on-disk layout,
project.json, shotplan validation, and the NAS mirror.

Layout (per project):
  project.json        {slug,title,status,created,updated,approval,nas_sync,assets}
  bible/characters/<char_id>/, bible/environments/<env_id>/, bible/voices/
  storyboard/         script.md, shotplan.json, keyframes/, pages/
  production/         clips/ (takes never overwritten), audio/, final/
  takes.json          {shot_id: [{job_id,seed,prompt,model,path,active}]}
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

RUN_DIR = Path("/home/chuck/data/comfyui/run")
PROJECTS_ROOT = Path(os.environ.get("MEDIA_PROJECTS_ROOT", str(RUN_DIR / "media_projects")))
NAS_ROOT = Path(os.environ.get("MEDIA_NAS_ROOT", "/mnt/lego/media_projects"))
SLUG_RE = re.compile(r"^[a-z0-9_]{1,64}$")

# NAS sync is best-effort and never blocks a mutation. A CIFS share is
# visible from inside the container only while the host automount is active;
# `df --output=source` reports the CIFS share (//host/share) when mounted and
# a local block device when the automount has dropped — that is the probe.
_NAS_IO_TIMEOUT_S = 120
_SYNC_LOCK = threading.Lock()  # one rsync at a time (NAS is slow; serialize)

# ------------------------------------------------------------------ shotplan
# Two-tier shotplan (scene -> beats -> micro_shots) — the anti-suck-atude
# contract. Enums per films plan §2.4.
SHOT_CATEGORIES = ("dialogue_drama", "action_impact", "macro_insert", "establishing")
RISK_LEVELS = ("low", "high")
SHOT_TYPES = ("wide", "medium", "medium_close_up", "extreme_close_up")
ANIMATIC_MOVES = ("push_in", "pull_out", "pan_left", "pan_right", "shake", "static")
MAX_DURATION_FRAMES = 129        # wan14b-bound shots (4k+1)
ACTION_IMPACT_MAX_FRAMES = 49    # action_impact shots stay short
# keyframe prompts must be static compositions — motion verbs are a warning
# (stored as schema_warnings), not a failure.
MOTION_VERBS = (
    "run", "running", "runs", "walk", "walking", "walks", "jog", "jogging",
    "jump", "jumping", "jumps", "leap", "leaping", "leaps", "dive", "diving",
    "dive", "dash", "dashing", "dashes", "sprint", "sprinting", "sprints",
    "throw", "throwing", "throws", "swing", "swinging", "swings", "spin",
    "spinning", "spins", "lunge", "lunging", "lunges", "fall", "falling",
    "falls", "rise", "rising", "rises", "fly", "flying", "flies", "drive",
    "driving", "drives", "kick", "kicking", "kicks", "punch", "punching",
    "punches", "wave", "waving", "waves", "turn", "turning", "turns", "move",
    "moving", "moves", "shift", "shifting", "shifts", "reach", "reaching",
    "reaches", "grab", "grabbing", "grabs", "pull", "pulling", "pulls",
    "push", "pushing", "pushes", "lift", "lifting", "lifts", "drop",
    "dropping", "drops", "shake", "shaking", "shakes", "nod", "nodding",
    "tilt", "tilting", "tilts", "lean", "leaning", "leans", "crawl",
    "crawling", "climb", "climbing", "climbs", "flap", "flapping", "flaps",
    "wobble", "wobbling", "wobbles", "sway", "swaying", "sways", "roll",
    "rolling", "rolls", "tumble", "tumbling", "tumbles", "twist",
    "twisting", "twists", "whip", "whipping", "whips", "jerk", "jerking",
    "jolts", "hover", "hovering", "hovers", "soar", "soaring", "soars",
    "crash", "crashing", "crashes", "burst", "bursting", "bursts", "flick",
    "flicking", "flicks", "stumble", "stumbling", "stumbles", "hurdle",
    "hurdling", "hurdles", "gallop", "galloping", "crouch", "crouching",
    "dangle", "dangling", "dangles", "bob", "bobbing", "bobs", "dart",
    "darts", "lurch", "lurching", "lurches", "twirl", "twirling", "twirls",
)
_MOTION_RE = {v: re.compile(rf"\b{re.escape(v)}\b", re.IGNORECASE) for v in set(MOTION_VERBS)}


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ store
def project_dir(slug: str) -> Path:
    if not SLUG_RE.match(slug):
        raise ValueError(f"invalid project slug {slug!r} (use [a-z0-9_] 1-64 chars)")
    return PROJECTS_ROOT / slug


def exists(slug: str) -> bool:
    try:
        return (project_dir(slug) / "project.json").is_file()
    except ValueError:
        return False


def list_projects() -> list[dict]:
    """[{slug, title, status, updated, nas_pending}] for GET /projects."""
    out = []
    try:
        entries = sorted(PROJECTS_ROOT.iterdir())
    except OSError:
        return out
    for p in entries:
        pj = p / "project.json"
        if not pj.is_file():
            continue
        try:
            m = json.loads(pj.read_text())
            out.append({"slug": p.name, "title": m.get("title"),
                        "status": m.get("status"), "updated": m.get("updated"),
                        "nas_pending": bool(m.get("nas_sync", {}).get("pending"))})
        except (json.JSONDecodeError, OSError):
            out.append({"slug": p.name, "title": None, "status": "corrupt",
                        "updated": None, "nas_pending": None})
    return out


def load(slug: str) -> dict:
    p = project_dir(slug) / "project.json"
    if not p.is_file():
        raise FileNotFoundError(f"project not found: {slug}")
    return json.loads(p.read_text())


def save(slug: str, meta: dict) -> None:
    """Atomic project.json write (tmp + rename)."""
    p = project_dir(slug) / "project.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2) + "\n")
    tmp.replace(p)


def _touch(meta: dict) -> dict:
    meta["updated"] = _iso_now()
    return meta


def _open_up(d: Path) -> None:
    """Make a service-created dir writable by the comfy uid. The service runs
    as root; ffmpeg runs as uid 1024 (comfy) in comfyui_backend and writes
    storyboard pages into project dirs — root-owned 755 dirs would block it."""
    try:
        os.chmod(d, 0o777)
    except OSError:
        pass


def create(slug: str, title: str) -> dict:
    """Create the project dir tree + project.json. Raises ValueError (bad
    slug) / FileExistsError (slug taken)."""
    root = project_dir(slug)
    if (root / "project.json").exists():
        raise FileExistsError(f"project exists: {slug}")
    for sub in ("bible/characters", "bible/environments", "bible/voices",
                "storyboard/keyframes", "storyboard/pages",
                "production/clips", "production/audio", "production/final"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for d in [root, *root.rglob("*")]:
        if d.is_dir():
            _open_up(d)
    meta = _touch({
        "slug": slug,
        "title": title,
        "status": "draft",
        "created": _iso_now(),
        "updated": None,
        "approval": {"storyboard": None, "by": None, "note": None},
        "nas_sync": {"last": None, "ok": None, "pending": False, "files": 0},
        "assets": {},
    })
    save(slug, meta)
    (root / "takes.json").write_text("{}\n")
    return meta


def register_asset(slug: str, rel_path: str, size: int) -> dict:
    """Record an uploaded asset in project.json's index (path -> {size, added})."""
    meta = load(slug)
    meta["assets"][rel_path] = {"size": size, "added": _iso_now()}
    save(slug, _touch(meta))
    nas_sync(slug)
    return meta


def save_shotplan(slug: str, shotplan: dict) -> dict:
    """Validate + save the two-tier shotplan. Returns {shotplan, warnings}.

    Warnings (motion verbs in keyframe prompts) are stored alongside the
    plan as a sibling top-level key `schema_warnings` — the caller's plan
    content is never mutated.
    """
    errors, warnings = validate_shotplan(shotplan)
    if errors:
        raise ValueError("shotplan validation failed: " + "; ".join(errors))
    doc = {"schema_warnings": warnings, "saved": _iso_now(), **shotplan}
    p = project_dir(slug) / "storyboard" / "shotplan.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    tmp.replace(p)
    meta = load(slug)
    save(slug, _touch(meta))
    nas_sync(slug)
    return {"shotplan": str(p), "warnings": warnings}


def load_shotplan(slug: str) -> dict:
    p = project_dir(slug) / "storyboard" / "shotplan.json"
    if not p.is_file():
        raise FileNotFoundError(f"no shotplan saved for project {slug}")
    return json.loads(p.read_text())


def micro_shots(shotplan: dict) -> list[dict]:
    """Flatten a shotplan to micro_shots in deterministic (plan) order."""
    out = []
    for beat in shotplan.get("beats", []):
        for ms in beat.get("micro_shots", []):
            out.append(ms)
    return out


def validate_shotplan(sp: dict) -> tuple[list[str], list[dict]]:
    """Return (errors, warnings). errors non-empty -> 400 with the exact
    field list; warnings (motion verbs in keyframe_prompt) never fail."""
    errors: list[str] = []
    warnings: list[dict] = []
    if not isinstance(sp, dict):
        return ["shotplan must be a JSON object"], warnings
    if not str(sp.get("scene_id") or "").strip():
        errors.append("scene_id is required")
    beats = sp.get("beats")
    if not isinstance(beats, list) or not beats:
        errors.append("beats is required (non-empty list)")
        return errors, warnings
    seen_ids: set[str] = set()
    for bi, beat in enumerate(beats):
        b = f"beats[{bi}]"
        if not isinstance(beat, dict):
            errors.append(f"{b} must be an object")
            continue
        if not str(beat.get("beat_id") or "").strip():
            errors.append(f"{b}.beat_id is required")
        if not str(beat.get("narrative_goal") or "").strip():
            errors.append(f"{b}.narrative_goal is required")
        ms_list = beat.get("micro_shots")
        if not isinstance(ms_list, list) or not ms_list:
            errors.append(f"{b}.micro_shots is required (non-empty list)")
            continue
        for si, ms in enumerate(ms_list):
            m = f"{b}.micro_shots[{si}]"
            if not isinstance(ms, dict):
                errors.append(f"{m} must be an object")
                continue
            sid = str(ms.get("shot_id") or "").strip()
            if not sid:
                errors.append(f"{m}.shot_id is required")
            elif sid in seen_ids:
                errors.append(f"{m}.shot_id {sid!r} is not unique")
            else:
                seen_ids.add(sid)
            if ms.get("shot_category") not in SHOT_CATEGORIES:
                errors.append(f"{m}.shot_category must be one of {list(SHOT_CATEGORIES)}")
            if ms.get("risk_level") not in RISK_LEVELS:
                errors.append(f"{m}.risk_level must be one of {list(RISK_LEVELS)}")
            if ms.get("shot_type") not in SHOT_TYPES:
                errors.append(f"{m}.shot_type must be one of {list(SHOT_TYPES)}")
            if ms.get("animatic_move") not in ANIMATIC_MOVES:
                errors.append(f"{m}.animatic_move must be one of {list(ANIMATIC_MOVES)}")
            df = ms.get("duration_frames")
            if isinstance(df, bool) or not isinstance(df, int) or df <= 0:
                errors.append(f"{m}.duration_frames must be a positive integer")
            else:
                if (df - 1) % 4 != 0:
                    errors.append(f"{m}.duration_frames must be 4k+1 (wan14b-bound): {df}")
                if df > MAX_DURATION_FRAMES:
                    errors.append(f"{m}.duration_frames must be <= {MAX_DURATION_FRAMES}: {df}")
                if ms.get("shot_category") == "action_impact" and df > ACTION_IMPACT_MAX_FRAMES:
                    errors.append(f"{m}.duration_frames: action_impact shots must be "
                                  f"<= {ACTION_IMPACT_MAX_FRAMES}f: {df}")
            kp = str(ms.get("keyframe_prompt") or "").strip()
            if not kp:
                errors.append(f"{m}.keyframe_prompt is required")
            elif not errors:
                hits = sorted({v for v, rx in _MOTION_RE.items() if rx.search(kp)})
                if hits:
                    warnings.append({"shot_id": sid or f"{m}.shot_id",
                                     "field": "keyframe_prompt",
                                     "warning": "motion verb(s) in a static keyframe "
                                                f"prompt: {', '.join(hits)}"})
            if not str(ms.get("motion_prompt") or "").strip():
                errors.append(f"{m}.motion_prompt is required")
            if ms.get("use_end_frame_anchor") and not str(ms.get("end_keyframe_prompt") or "").strip():
                errors.append(f"{m}.end_keyframe_prompt is required when "
                              f"use_end_frame_anchor is true")
    return errors, warnings


def approve(slug: str, what: str, note: str | None = None, by: str | None = None) -> dict:
    """Set an approval gate (idempotent). v1: what='storyboard' only."""
    if what != "storyboard":
        raise ValueError(f"unknown approval target {what!r} (v1: 'storyboard' only)")
    meta = load(slug)
    ts = _iso_now()
    meta["approval"] = {"storyboard": ts, "by": by or "unknown", "note": note or ""}
    meta["status"] = "storyboard_approved"
    save(slug, _touch(meta))
    nas_sync(slug)
    return meta


# ------------------------------------------------------------------ takes
def register_take(slug: str, shot_id: str, entry: dict) -> dict:
    """Append a finished shot take to takes.json (takes are never
    overwritten; the newest take is active). `entry` = {job_id, seed, prompt,
    model, path}; the take number + active flag are assigned here."""
    root = project_dir(slug)
    tp = root / "takes.json"
    takes = json.loads(tp.read_text()) if tp.is_file() else {}
    lst = takes.get(shot_id, [])
    for t in lst:
        t["active"] = False
    n = len(lst) + 1
    lst.append({**entry, "take": n, "active": True, "created": _iso_now()})
    takes[shot_id] = lst
    tmp = tp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(takes, indent=2) + "\n")
    tmp.replace(tp)
    # The clip is copied into production/clips/<shot_id>_<take>.mp4 by the
    # caller (server.py) before this call, so the NAS mirror picks it up.
    nas_sync(slug)
    return takes


# ------------------------------------------------------------------ NAS mirror
def _nas_mounted() -> bool:
    """True if the NAS share backing NAS_ROOT is actually mounted (CIFS
    source in df). A dropped automount leaves a plain local dir behind —
    rsync into it would silently write to the wrong place, so this probe is
    mandatory before every sync."""
    try:
        r = subprocess.run(["df", "--output=source", str(NAS_ROOT.parent)],
                           capture_output=True, text=True, timeout=30)
        lines = [l for l in r.stdout.strip().splitlines() if l]
        return bool(lines) and lines[-1].startswith("//")
    except Exception:
        return False


def _count_files(root: Path) -> int:
    n = 0
    try:
        for p in root.rglob("*"):
            if p.is_file():
                n += 1
    except OSError:
        pass
    return n


def nas_sync(slug: str, force: bool = False) -> dict:
    """Append-only rsync of the project dir to the NAS. Never raises — a NAS
    failure must never fail a job. The mount probe ALWAYS runs (even for
    forced syncs): an unmounted share leaves a plain local dir behind, and
    rsyncing into it would silently write to the wrong place. On failure:
    nas_sync.pending=true, retried on the next mutation + the hourly
    retention-loop pass. `force` is accepted for API symmetry (sync now)."""
    if not exists(slug):
        return {"ok": False, "pending": False, "reason": "project not found"}
    root = project_dir(slug)
    meta = load(slug)
    if not _nas_mounted():
        meta["nas_sync"] = {"last": meta["nas_sync"].get("last"),
                            "ok": False, "pending": True,
                            "files": meta["nas_sync"].get("files", 0),
                            "reason": "nas not mounted"}
        save(slug, meta)
        return {"ok": False, "pending": True, "reason": "nas not mounted",
                "files": 0}
    dest = NAS_ROOT / slug
    files = _count_files(root)
    try:
        with _SYNC_LOCK:
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                ["rsync", "-a", "--ignore-existing", f"--timeout={_NAS_IO_TIMEOUT_S}",
                 str(root) + "/", str(dest) + "/"],
                capture_output=True, text=True, timeout=_NAS_IO_TIMEOUT_S * 3)
        if r.returncode != 0:
            raise RuntimeError(f"rsync failed: {(r.stderr or r.stdout)[-500:]}")
        synced = _count_files(dest)
        meta = load(slug)  # re-read (mutation may have raced)
        meta["nas_sync"] = {"last": _iso_now(), "ok": True, "pending": False,
                            "files": synced, "reason": None}
        save(slug, meta)
        return {"ok": True, "pending": False, "files": synced,
                "local_files": files, "dest": str(dest)}
    except Exception as e:  # noqa: BLE001 - NAS failure must never fail a job
        meta = load(slug)
        meta["nas_sync"] = {"last": meta["nas_sync"].get("last"),
                            "ok": False, "pending": True,
                            "files": meta["nas_sync"].get("files", 0),
                            "reason": str(e)[:300]}
        save(slug, meta)
        return {"ok": False, "pending": True, "reason": str(e)[:300], "files": 0}


def nas_pending_sync() -> list[dict]:
    """Hourly retry pass (called from the retention loop): sync every
    project with nas_sync.pending=true. Never raises."""
    out = []
    try:
        slugs = [p.name for p in sorted(PROJECTS_ROOT.iterdir())
                 if (p / "project.json").is_file()]
    except OSError:
        return out
    for slug in slugs:
        try:
            meta = load(slug)
            if meta.get("nas_sync", {}).get("pending"):
                out.append(nas_sync(slug, force=True))
        except Exception:  # noqa: BLE001
            continue
    return out
