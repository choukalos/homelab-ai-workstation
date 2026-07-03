# Embeddings Decision: Matrix vs. Thor

> Created: 2026-07-03
> Status: **DECISION — Keep on Matrix (Interim)**
> Review: Before Phase 15 production deployment

## Current Architecture

| Component | Location | Transport |
|---|---|---|
| Embedding model (nomic-embed-text) | Matrix — Ollama (port 11434) | — |
| LiteLLM proxy | Thor | `http://matrix:11434` |
| Vector DB (Qdrant/Chroma) | **Not yet deployed** | — |

### Current Flow

```
Thor (LiteLLM) → http://matrix:11434/api/embeddings → nomic-embed-text (Ollama) → vector result
```

## Model Characteristics

| Property | Value |
|---|---|
| Model | nomic-embed-text |
| Size | 274 MB |
| Embedding dimensions | 768 |
| Latency (warm, single) | ~9-34 ms |
| GPU VRAM used | < 100 MB (runs inside Ollama, which holds ~878 MB total for all models) |
| Shared infrastructure | Same Ollama instance as Gemma4-26B (17 GB VRAM) |

## Analysis: Keep on Matrix ✅

### Arguments FOR keeping on Matrix

1. **Negligible resource cost** — 274 MB model, <100 MB VRAM, runs in the same Ollama process already serving Gemma4. Adding it creates essentially zero marginal cost.

2. **Zero network overhead** — Embeddings are already on the same machine as the GPU. No cross-network hop for inference.

3. **Simplicity** — One fewer service to manage on Thor. One fewer network dependency for the embeddings path.

4. **Already wired and working** — Thor's LiteLLM points to `http://matrix:11434`. No config changes, no client changes.

5. **No GPU contention** — nomic-embed-text runs on Ollama's CPU/GPU hybrid path. At 274 MB it barely touches the 72 GB GPU. Gemma4 (17 GB) dominates Ollama's VRAM use.

6. **Vector DB colocation logic** — When a vector DB (Qdrant/Chroma) is eventually deployed, Matrix has abundant CPU/RAM/disk headroom. Co-locating the vector DB with embeddings on Matrix keeps the data path local.

### Arguments FOR moving to Thor

1. **Conceptual purity** — If Thor owns all application logic (LiteLLM, apps, DB), embeddings feels like an application concern, not a compute concern.

2. **Single failure domain** — If Matrix goes down, both the primary LLM and embeddings are lost. This is already true for vLLM anyway.

3. **CPU-only option** — nomic-embed-text is small enough to run well on CPU on Thor, freeing the Matrix Ollama instance for Gemma4 alone.

### Why the Thor arguments don't outweigh them

- **Point 1 (purity)** — Matrix is defined as a compute appliance. Embeddings *is* compute. This fits the boundary.
- **Point 2 (failure domain)** — vLLM (the primary model) is already on Matrix. Losing Matrix kills the main model regardless of where embeddings live.
- **Point 3 (CPU-only)** — Possible, but adds a new Ollama install/management surface on Thor for a 274 MB model. Not worth the operational complexity.

## Decision

**Keep embeddings on Matrix (via Ollama) for the foreseeable future.**

### Rationale

The embedding model is tiny (274 MB), runs on shared infrastructure that's already paid for (Ollama + GPU), and incurs zero additional operational overhead. Moving it to Thor would add complexity (another Ollama instance, network dependency) for no measurable benefit.

### Conditions for Revisiting

Re-evaluate if any of the following change:
- Embedding model grows significantly (e.g., switching to a 7B+ embedding model)
- A vector DB is deployed on Thor and local embeddings become beneficial for that architecture
- Ollama on Matrix becomes a bottleneck (unlikely given current load)
- Matrix needs every MB of VRAM reclaimed (embeddings uses <100 MB)

### Interim Rule (from matrix_todo.md)

> Keep current embeddings path working. Do not move embeddings during the Matrix refactor.

## Future: Vector DB

When a vector DB (Qdrant/Chroma/Weaviate) is eventually deployed, the recommendation is to **colocate it on Matrix** alongside embeddings:
- Matrix has abundant CPU/RAM/disk headroom
- Keeps the embeddings → vector store data path local (no network hop)
- Consistent with Matrix as a compute appliance

## Impact on Other Phases

| Phase | Impact |
|---|---|
| Phase 10 (ComfyUI) | None — embeddings on Ollama is independent |
| Phase 15 (Production) | No changes needed — keep current path |
| Model Manager (Phase 4) | Embeddings profile already exists in `models/profiles/embeddings.yaml` |
