# voices_thor_handoff — Part 2: expose the TTS voice library in mcp_media (thor)

**To:** Thor agent (MCP host)
**From:** Matrix agent (GPU host)
**Date:** 2026-09-25
**Status:** Matrix side **DONE & verified** (2026-09-25). This is the remaining
work on **thor**. Self-contained — you do not need matrix code or repo access;
the full API contract is in §2 and the exact code changes in §3.

---

## 1. TL;DR

The media pipeline on matrix (`http://192.168.4.55:8189`) now has a **TTS voice
library**: `GET /voices`, `POST /voices`, `DELETE /voices/{name}`, and `/tts`
`voice` accepts a **library name** or a **reference wav path** (the raw-path
contract was previously documented but broken — the server never forwarded
`--reference-audio`; it now does). Current portfolio:

| voice | what it is | F0 (measured) |
|---|---|---|
| `trailer` | deep male movie-trailer narrator (**default**, protected) | ~123 Hz |
| `default` | stock male XTTS voice (protected) | ~193 Hz |
| `deep_m` | warm deep male (pitch-shifted clone) | ~150 Hz |
| `narrator_f` | **female** narrator (pitch-shifted clone) | ~212 Hz |

**Your job:** add 3 client methods + 3 MCP tools to the `mcp_media` server on
thor, update the `media_text_to_speech` docstring, rebuild/restart the
`ai-mcp-mcp_media` container, and run the verification checklist (§6).
Nothing breaks: all changes are additive and backwards compatible.

**Why this matters to users:** every video VO has sounded like the same male
trailer voice. After this, agents can pick `narrator_f` (female), `deep_m`,
etc. via `media_list_voices`, and can register new voices from any 3–15 s
reference clip via `media_add_voice`.

---

## 2. API contract (matrix :8189, verified live 2026-09-25)

Base: `http://192.168.4.55:8189` (LAN-only, no auth — same posture as all
other pipeline endpoints). Identity fields `user`/`client` accepted on job
routes (metering).

### 2.1 `GET /voices` (sync)

Response `200`:
```json
{
  "voices": [
    {
      "name": "narrator_f",
      "description": "Bright female narrator (pitch-shifted clone of the stock sample, ~4 semitones up). Good for documentary-style VO.",
      "gender": "female",
      "style": "narrator",
      "added": "2026-09-25T01:03:53Z",
      "protected": false,
      "ref_exists": true,
      "sample": "/home/chuck/data/comfyui/basedir/models/tts/voices/narrator_f_sample.wav"
    }
  ]
}
```
Fields: `name`, `description` (agent-facing), `gender` (`female|male|unknown`),
`style`, `added` (ISO timestamp), `protected` (bool; `trailer`/`default` are
true), `ref_exists` (bool), `sample` (GPU-host path to an audition wav, or
`null`).

### 2.2 `POST /voices` (job flow — GPU work for the sample)

Request JSON:
```json
{
  "name": "my_voice",
  "source": "/home/chuck/data/comfyui/run/media_jobs/uploads/123_ref.wav",
  "description": "short human description (agent-facing)",
  "gender": "female",
  "style": "narrator",
  "sample_text": "optional override of the sample line",
  "user": "chuck",
  "client": "pi"
}
```
- `name`: lowercase slug `[a-z0-9_]{2,32}` (400 otherwise).
- `source`: GPU-host path to a reference wav — absolute under the run dir
  (`/home/chuck/data/comfyui/run/…`) or basedir (`/home/chuck/data/comfyui/basedir/…`),
  or run-dir-relative (e.g. `media_jobs/uploads/…`). 400 if outside those roots
  or missing.
- Reference QC: duration **3–15 s** (400 outside); normalized to 16 kHz mono
  server-side.
- Re-registering an existing name **replaces** its ref/sample (manifest `added`
  date preserved). Protected names (`trailer`/`default`) → 400.

Flow: `200 {"job_id": "…"}` → poll `GET /jobs/{job_id}` → on `done`:
```json
{"voice": "my_voice",
 "ref": "/home/chuck/data/comfyui/basedir/models/tts/voices/my_voice_ref.wav",
 "sample": "/home/chuck/data/comfyui/basedir/models/tts/voices/my_voice_sample.wav",
 "ref_duration_s": 7.2}
```
On `error`: `error` field carries the reason (e.g. QC failure, ffmpeg error).
Typical runtime: ~15–40 s (one TTS sample generation on the GPU queue).

### 2.3 `DELETE /voices/{name}` (sync)

- `200` → `{"deleted": "my_voice"}` (ref + sample + manifest entry removed)
- `400` → `{"detail": "voice 'trailer' is protected"}`
- `404` → `{"detail": "voice 'nope' not found"}`

### 2.4 `POST /tts` — changed `voice` semantics

`voice` is now resolved **server-side** (worker unchanged):
1. `trailer` / `default` → legacy `--voice` pass-through (unchanged behavior)
2. a library name from `GET /voices` → `--reference-audio <resolved ref>`
3. a path (contains `/` or ends `.wav`) → `--reference-audio <that path>`
   (must be under the run/basedir roots — **this is the previously broken
   contract, now working**)
4. anything else → **400** with
   `{"detail": "unknown voice 'x'. Available: default, trailer, … (or pass a
   reference wav path, or 'reference_audio')"}`

An explicit `reference_audio` field in the payload takes precedence over
`voice` (belt-and-braces). No-voice payloads still default to `trailer`.

### 2.5 Auditioning a voice (no new endpoint needed)

`GET /voices` → `POST /tts {"text": "<short line>", "voice": "<name>"}` →
`POST /dl_token {"path": "<vo.wav host path>", "ttl_hours": 48}` → share
`http://192.168.4.55:8189/dl/<token>` (or use the existing `media_pull` /
`media_fetch_dl` MCP tools).

---

## 3. Code changes (repo `media-mcp-client/`, deployed to thor)

Two files. The snippets below match the existing code style (urllib, no new
deps). **Reconcile first** (§5.1) — the live thor container may be ahead of
this repo's copy; merge, don't clobber.

### 3.1 `media_pipeline_client.py`

**a) `text_to_speech` — docstring only** (the `voice` param is already passed
through verbatim; no logic change needed):

```python
    def text_to_speech(self, text: str, voice: str = "trailer",
                       timeout: float = 1800) -> str:
        """Script -> voice-over wav. Returns GPU-host path. `voice` = a library
        name (see list_voices: trailer, default, narrator_f, deep_m, ...) or a
        reference wav path on the GPU host (3-15 s single-speaker clip)."""
        return self._wait(self._post_json("/tts", {"text": text, "voice": voice}),
                          timeout)["audio"]
```

**b) three new methods** (add after `text_to_speech`, or near the other sync
helpers):

```python
    # ------------------------------------------------- voice library (2026-09-25)
    def list_voices(self) -> list:
        """GET /voices (sync) -> [{name, description, gender, style, added,
        protected, ref_exists, sample}]."""
        code, body, _ = self._get("/voices", timeout=30)
        if code != 200:
            raise PipelineError(f"/voices -> HTTP {code}: {body[:200]!r}")
        return json.loads(body)["voices"]

    def add_voice(self, name: str, source: str, description: str = "",
                  gender: str = "", style: str = "", sample_text: str = "",
                  timeout: float = 900) -> dict:
        """POST /voices (job): register a voice from a reference wav (3-15 s).
        `source` = GPU-host path under the run/basedir dirs (stage external
        files with download_url/upload_file first). Returns the job output
        {voice, ref, sample, ref_duration_s}."""
        payload = {"name": name, "source": source, "description": description,
                   "gender": gender, "style": style}
        if sample_text:
            payload["sample_text"] = sample_text
        return self._wait(self._post_json("/voices", payload), timeout)

    def delete_voice(self, name: str) -> dict:
        """DELETE /voices/{name} (sync). 400 on protected names, 404 missing.
        Returns {"deleted": name}."""
        req = urllib.request.Request(f"{self.base}/voices/{name}", method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise PipelineError(f"/voices/{name} -> HTTP {e.code}: {e.read()[:200]!r}")
```

Note: `_post_json` already injects `user`/`client` identity, so `add_voice`
gets metering attribution for free. `list_voices`/`delete_voice` are sync
(unauthenticated, no identity needed).

### 3.2 `mcp_tools.py`

**a) `media_text_to_speech` — docstring update** (logic unchanged):

```python
@mcp.tool()
def media_text_to_speech(text: str, voice: str = "trailer") -> str:
    """Generate voice-over speech via the GPU-host media pipeline. `voice` = a
    library name (see media_list_voices: trailer, default, narrator_f, deep_m,
    ...) or a path to a custom reference wav on the GPU host. movie-trailer
    voice by default. Audition flow: media_list_voices -> this tool with a
    short line -> media_pull for a signed URL. Returns a wav path."""
    return _localize(pipe.text_to_speech(text, voice), "vo")
```

**b) three new tools** (add after `media_text_to_speech`):

```python
@mcp.tool()
def media_list_voices() -> list:
    """List the TTS voice library: name, description, gender, style, and the
    audition sample path for each. Call this before generating VO to pick a
    voice (e.g. narrator_f for a female narrator)."""
    return pipe.list_voices()


@mcp.tool()
def media_add_voice(name: str, source: str, description: str = "",
                    gender: str = "", style: str = "") -> dict:
    """Register a new TTS voice from a reference wav on the GPU host
    (3-15 s of clean single-speaker speech; QC'd, normalized to 16 kHz mono,
    audition sample generated on the GPU). `source` = GPU-host path under the
    run/basedir dirs — stage external files with media_download_url or
    media_upload_file first (they land in media_jobs/uploads/). Re-registering
    a name replaces it. Returns {voice, ref, sample, ref_duration_s}."""
    return pipe.add_voice(name, source, description, gender, style)


@mcp.tool()
def media_delete_voice(name: str) -> dict:
    """Remove a TTS voice (ref + sample + manifest entry). `trailer` and
    `default` are protected (400). Returns {"deleted": name}."""
    return pipe.delete_voice(name)
```

**c) README/HANDOFF sync** (repo `media-mcp-client/`): the tool table in
`README.md` and the contract/tool tables in `HANDOFF.md` already document the
three new tools (marked ⏳ pending) as of 2026-09-25 — remove the ⏳ markers
once deployed.

---

## 4. What the tools will look like to agents (post-deploy)

- `media_list_voices` → the 4-voice table from §1 (with descriptions)
- `media_text_to_speech("…", voice="narrator_f")` → female VO (verified on
  matrix: F0 ~212 Hz vs ~123 Hz for trailer)
- `media_add_voice("family_f", "media_jobs/uploads/…_ref.wav", …)` → new voice
  with a generated audition sample
- `media_delete_voice("family_f")` → cleanup

---

## 5. Deploy to thor

### 5.1 Reconcile first (do NOT clobber)

The live `mcp_media` container may be **ahead** of the repo copy (it has extra
tools like `media_put`/`media_pull`/`media_fetch` that predate or postdate the
repo). Before editing:

1. Locate the running server's `mcp_tools.py` + `media_pipeline_client.py`
   (the media-mcp server dir on thor; container `mcp_media`, image
   `ai-mcp-mcp_media:latest`).
2. `diff` them against `media-mcp-client/` in the homelab repo.
3. Apply §3 **as a merge** onto the live files (keep the extra live tools).
4. Copy the merged files back into the repo so the repo is the source of truth
   again.

### 5.2 Rebuild + restart

```bash
# on thor, in the media-mcp server dir (adjust to the actual layout)
docker build -t ai-mcp-mcp_media:latest .
docker restart mcp_media          # or the compose equivalent
```

### 5.3 Smoke test

In a fresh pi session (or via the MCP client): the tool list must include
`media_list_voices`, `media_add_voice`, `media_delete_voice` alongside the
existing 17.

---

## 6. Verification checklist (MCP level, from thor)

- [ ] `media_list_voices` returns `trailer`, `default`, `narrator_f`, `deep_m`
      with descriptions + sample paths.
- [ ] `media_text_to_speech("The quick brown fox jumps over the lazy dog.",
      voice="narrator_f")` → wav; `media_pull` (signed URL) → **listen: female**.
- [ ] `media_text_to_speech` with `voice=<GPU-host path to a ref wav>` works
      (the previously broken contract).
- [ ] Default call (no `voice`) → still the male trailer voice (unchanged).
- [ ] Unknown voice → clean 400 error surfaced (not a worker crash).
- [ ] `media_add_voice` end-to-end: stage a 6–10 s test clip (e.g.
      `media_download_url` of a public-domain speech sample, or a local file
      via `media_upload_file`), register as `test_v`, confirm the sample
      generates, then `media_delete_voice("test_v")` → gone from
      `media_list_voices`.
- [ ] Existing flows unaffected: one `media_generate_image` + one
      `media_assemble` smoke job.
- [ ] Remove the ⏳ markers in `media-mcp-client/README.md` + `HANDOFF.md`;
      update the `kb_homelab` fact (id
      `a7955657-2f72-04b9-af65-890a985200de` — "TTS voice inventory") with the
      MCP-level status; commit `media-mcp-client/` changes.

---

## 7. Notes & gotchas

- **Backwards compatibility:** `trailer`/`default` names, defaults, and old job
  payloads are unchanged; the TTS worker CLI is unchanged. No matrix-side
  action needed for this part.
- **Voice quality:** all four seeded voices are XTTS-v2 **zero-shot clones of
  the same stock speaker** (pitch-shifted). `narrator_f` is "fine for VO", not
  broadcast. A real recorded reference (3–15 s clean single-speaker speech)
  registered via `media_add_voice` clones far better — that's the path to
  genuinely new voices (e.g. a family member's voice with consent).
- **ACE-Step (songs):** vocal gender in generated music is **prompt-driven** —
  write "female lead vocals" / "male baritone" into the `media_generate_music`
  prompt; unspecified often comes out male. No code change.
- **Retention:** voice files live under
  `/home/chuck/data/comfyui/basedir/models/tts/voices/` — outside the 14-day
  `media_jobs` sweep. Registered voices are permanent until deleted.
- **Security:** `/voices*` follows the pipeline's LAN-only, no-auth posture;
  public delivery stays via signed `/dl` tokens only.
- **Out of scope (future):** ElevenLabs / Fish-Audio backend (ComfyUI nodes
  already on matrix), multilingual VO (worker hardcodes `language="en"`),
  voice picker UI.
- **Matrix-side plan + verification log:** `voices_todo.md` (repo root,
  matrix side). Canonical API doc: `docs/matrix_media_pipeline_api.md`.