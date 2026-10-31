# Thor handoff: `media_generate_shot` gains a `model` param (Wan 14B I2V)

> Created: 2026-10-25 · Updated: 2026-10-31 (post-QA: 720p dropped, default flipped) · For: **thor** — update the deployed `mcp_media` server.
> Source of truth: `mcp/servers/media` in [choukalos/homelab-ai-harness](https://github.com/choukalos/homelab-ai-harness)
> (the local handoff snapshot in `media-mcp-client/` was removed 2026-09-27 —
> **pull current `main` first; do not apply this against any cached/stale copy**).

## Context

Matrix's media-pipeline `/shots` flow has a second I2V model:
**Wan 2.2 14B** (`model=wan14b`, quality — **832×480 only**, 81f @ 16 fps,
~10–12 min/clip; 720p failed the VRAM gate 2026-10-25 — OOM — and is rejected
at the API with a clean 400) alongside the existing **LTXV 2B**
(`model=ltxv2b`, fast draft — 768×512, 97f @ 25 fps, ~8 s/clip). The pipeline
server (Matrix :8189) accepts a `model` form field on `POST /shots`; omitted
`width`/`height`/`frames`/`fps` fields make the server apply the selected
model's native defaults. **QA passed 2026-10-31 and the default is now
`MEDIA_SHOT_MODEL=wan14b`** — so until this MCP update lands, the thor tool's
hardcoded 768×512/97f/24fps fields will 400 against the wan14b default
(768×512 is not a wan14b resolution). **This update is required, not optional**
(it only forwards fields — it never hardcodes a model).

This snippet is **self-contained**: it replaces two named functions in full
(no context diff), so it applies cleanly against current `main`.

## 1. `mcp/servers/media/media_pipeline_client.py`

Replace the `generate_shot` method (currently: positional
`width=768, height=512, frames=97, fps=24.0` defaults, no `model`) with:

```python
    def generate_shot(self, keyframe: str, prompt: str, width: int | None = None,
                      height: int | None = None, frames: int | None = None,
                      fps: float | None = None, seed: int = 42,
                      strength: float = 0.7, model: str | None = None,
                      user: str | None = None, client: str | None = None,
                      non_blocking: bool = False, timeout: float = 3600) -> str | dict:
        """Keyframe (LOCAL path) + style prompt -> I2V clip. Returns host path.

        model: 'ltxv2b' (fast draft; LTXV 2B 0.9.6 distilled — 768x512, 97f,
        25fps, ~8s/clip) | 'wan14b' (quality; Wan 2.2 14B I2V GGUF Q5 —
        832x480 ONLY, 81f (4n+1), 16fps, ~10-12 min/clip; 720p is rejected
        with a 400 — VRAM OOM gate 2026-10-25). None = server default
        (MEDIA_SHOT_MODEL; wan14b as of 2026-10-31). Omitted
        width/height/frames/fps -> the server applies the selected model's
        native defaults.
        strength: ltxv2b only — how strongly the keyframe anchors the clip
        (0.7 is the tuned default; lower = less warble). Ignored for wan14b.
        non_blocking=True -> submit only; returns {"job_id"} immediately.
        """
        fields = {"prompt": prompt, "seed": str(seed),
                  "strength": str(strength)}
        for k, v in (("width", width), ("height", height),
                     ("frames", frames), ("fps", fps)):
            if v is not None:
                fields[k] = str(v)
        if model is not None:
            fields["model"] = model
        jid = self._post_multipart("/shots", keyframe, fields, user, client)
        if non_blocking:
            return {"job_id": jid}
        return self._wait(jid, timeout)["video"]
```

## 2. `mcp/servers/media/server.py`

Replace the entire `@mcp.tool(name="media_generate_shot", ...)` block
(decorator + `async def media_generate_shot(...)`) with:

```python
@mcp.tool(
    name="media_generate_shot",
    description=(
        "Animate a keyframe into a video clip (I2V) via the GPU-host media "
        "pipeline. `model`: 'wan14b' (quality; Wan 2.2 14B — 832x480 only, "
        "~5s clip, ~10-12 min) | 'ltxv2b' (fast draft; LTXV 2B — 768x512, "
        "~4s clip, ~8s). Omitted = server default (wan14b as of 2026-10-31). "
        "Omitted width/height/frames/fps -> model-native defaults. `keyframe` "
        "may be a local image path OR a GPU-host path (auto-fetched). "
        "`prompt` should describe VISUAL STYLE (not fast motion) to minimize "
        "warble. `strength` = how strongly the keyframe anchors the clip "
        "(ltxv2b only; 0.7 is the tuned default; ignored for wan14b). "
        "Returns {path, location='gpu_host'}. non_blocking=true submits "
        "without waiting — returns {job_id} in ~1s; poll "
        "media_job_result(job_id) for the result."
    ),
)
async def media_generate_shot(
    keyframe: str,
    prompt: str,
    width: Optional[int] = None,
    height: Optional[int] = None,
    frames: Optional[int] = None,
    fps: Optional[float] = None,
    seed: int = 42,
    strength: float = 0.7,
    model: Optional[str] = None,
    non_blocking: bool = False,
    ctx: Context = None,
) -> dict:
    """Keyframe -> I2V clip (GPU host). model: wan14b (quality) | ltxv2b (fast)."""
    user = await asyncio.to_thread(_resolve_user, ctx)
    try:
        local_kf = await _ensure_local(keyframe)
        if not os.path.isfile(local_kf):
            return {"error": f"Keyframe not found (local or on GPU host): {keyframe}"}
        res = await asyncio.to_thread(
            PIPELINE.generate_shot, local_kf, prompt, width, height, frames, fps,
            seed, strength, model, user=user, client=MEDIA_CLIENT,
            non_blocking=non_blocking,
        )
    except Exception as exc:
        return _pipeline_error(exc, {"keyframe": keyframe, "prompt": prompt})
    if non_blocking:
        return _non_blocking_result(res)
    return {"path": res, "location": "gpu_host", "note": _HOST_PATH_NOTE}
```

Also update the module docstring line (top of `server.py`):

```
- media_generate_shot(keyframe, prompt, ...)     Keyframe -> I2V clip (model: wan14b quality | ltxv2b fast)
```

## 3. `mcp/servers/media/README.md`

In the tool table, update the `media_generate_shot` row to:

```
| `media_generate_shot` | job | `keyframe, prompt, width?, height?, frames?, fps?, seed, strength, model, non_blocking` | Keyframe -> I2V clip. `model`: `wan14b` (quality, Wan 2.2 14B, ~5s clip, ~10-12 min, 832x480 only) \| `ltxv2b` (fast draft, LTXV 2B, ~4s clip, ~8s); omitted = server default (wan14b). Dims/frames/fps omitted = model-native defaults. `strength` is ltxv2b-only. |
```

(merge the new params into the existing row's shape; keep the row's other
columns as-is.)

## 4. Verify (on thor)

1. `git pull` current `main`, apply §1–§3, commit.
2. Rebuild/restart the `mcp_media` container from the repo.
3. Tool schema check: the tool list for `media_generate_shot` now shows
   `model` (optional string) and `width/height/frames/fps` as optional.
4. Smoke tests (Matrix pipeline support is LIVE as of 2026-10-31 — QA passed,
   default is `wan14b`):
   - `media_generate_shot(keyframe=<recent media_jobs png>, prompt="slow push-in",
     model="ltxv2b", non_blocking=true)` → done; `media_info` ≈4 s clip
     (LTXV path intact).
   - No `model` + no dims → server default = **wan14b 832×480 81f** → done in
     ~10–12 min; `media_info` = 832×480, ≈5.1 s.
   - `model="wan14b"` + `width=1280, height=720` → clean 400 (720p rejected).
5. If e2e tests exist in the repo (`mcp/servers/media/`), run them.

## Notes

- **⚠️ Breaking for old callers (2026-10-31):** the default flipped to
  `wan14b`, and the old tool always sent 768×512/97f/24fps — those dims are
  invalid for wan14b (400). Apply this update (dims become optional) and
  restart `mcp_media` so the old hardcoded dims stop being sent.
- `strength` is still forwarded for both models (harmless; the server ignores
  it for wan14b) so the tool signature stays stable.
- Matrix-side contract (live): `POST /shots` form fields `model` (optional),
  `width/height/frames/fps` (optional → model-native defaults). See
  `docs/matrix_media_pipeline_api.md` on Matrix.