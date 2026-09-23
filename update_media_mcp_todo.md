# update_media_mcp_todo — mcp_media client update for Qwen-Image-2.1 (2026-09-23)

> Hand this to the **Thor agent** (the one running the `mcp_media` MCP server
> that calls the matrix media-pipeline) AFTER the matrix pipeline upgrade
> (`media_todo.md` Phases 1–3) is green. The pipeline is **backwards
> compatible**, so the MCP client keeps working unchanged in the meantime —
> but its step defaults are wrong for the new model and it can't use the new
> features.

## Background

The matrix media-pipeline (`http://192.168.4.55:8189`) now defaults its
`/images` and `/images/edit` flows to **Qwen-Image-2.1** (ComfyUI v0.37.0
native nodes, int8_convrot weights). The legacy Qwen-Image-2512/2511 (GGUF +
Lightning) path is still available via `model=legacy`.

## Required changes in the mcp_media client

1. **Fix the step defaults** (the important one):
   - `generate_image`: `steps` default `4` → **`25`**
   - `edit_image`: `steps` default `8` → **`25`**
   - Why: 4/8 steps was only fast because of the Lightning distilled LoRAs.
     Qwen-Image-2.1 has no distilled LoRA at launch (official range 25–40).
     The server clamps qwen21 steps to [10, 50], so a stale `steps=4` caller
     silently runs 10 steps — degraded quality, ~4× slower than intended.
     (The legacy path still honors 4/8 if `model=legacy` is passed.)
2. **Expose `model`** (optional passthrough, default unset → server default):
   - `generate_image(..., model: str | None = None)` → JSON field `"model"`
   - `edit_image(..., model: str | None = None)` → form field `model`
   - Values: `"qwen21"` (default) | `"legacy"`. Lets the agent fall back to
     the old models if the new ones misbehave on a given prompt.
3. **Expose `references`** on `edit_image` (the killer feature):
   - `edit_image(..., references: list[str] | None = None)` → form field
     `references` (comma-joined). Up to 9 entries, each a **ComfyUI input/
     filename** or a **media_jobs-relative path** like
     `media_jobs/<job_id>/<file>.png` (the pipeline stages them into ComfyUI
     input/ server-side). `image_1` is always the image being edited;
     references are extra identity/consistency inputs (e.g. a character sheet
     or a previous shot's keyframe).
   - Typical use in the commercial pipeline: pass the first shot's keyframe as
     a reference when editing later shots so the character/product stays
     consistent.
4. **Update the tool descriptions** (so the calling LLM knows):
   - create: "Qwen-Image-2.1, 25 steps default, ~30–120 s at 1280×720"
   - edit: "unified editing model; supports up to 9 reference images for
     consistency; canvas follows the edited image"
5. **Optional:** raise the client-side timeout for image jobs (default
   `max_wait`) to ≥ 300 s if it isn't already — qwen21 at 25 steps is slower
   than the old 4-step path.

## Verification (after the update, from Thor)

```bash
# via the MCP tools, or directly:
curl -s -X POST http://192.168.4.55:8189/images \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a red bicycle leaning on a brick wall, soft morning light","width":1280,"height":720,"seed":42}'
# expect: job done in ~30–120 s, output image, metering model label qwen-image-2.1
```

- [ ] `generate_image` works with new defaults (25 steps)
- [ ] `edit_image` works with a single uploaded image
- [ ] `edit_image` with `references=[<media_jobs path>]` works (needs a
      previously generated image path from the pipeline)
- [ ] `model="legacy"` still works on both (regression)

## Rollback note

If the new path has issues, the pipeline can be flipped back globally with
`MEDIA_IMAGE_MODEL=legacy` in `/home/chuck/homelab/.env` + container recreate
(matrix side) — no MCP client change needed. Per-request `model="legacy"`
works either way.