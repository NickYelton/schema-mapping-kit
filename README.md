# Schema Mapping & Onboarding Kit

This tool ingests an arbitrary CSV/Excel/JSON file, profiles every column, proposes a mapping
to a target schema using embeddings + an LLM + dumb-but-effective heuristics, presents it in a
review UI where a human confirms or corrects, and emits a **versioned, deterministic transform**
(SQL or Python) that runs forever after without touching the LLM. Failed rows get a plain-English
rejection report the customer can actually act on.

> **The LLM is a design-time assistant, never a runtime dependency.**

## Stack

| Layer | Choice |
|---|---|
| API | Python 3.13 · FastAPI · uv |
| DataFrames | Polars |
| Catalog + SQL target | DuckDB |
| Validation | Pandera (polars backend) |
| Embeddings | model2vec `potion-retrieval-32M` — local, offline, no torch |
| LLM | Anthropic or OpenRouter, selected by `LLM_PROVIDER` |
| UI | React 19 · TypeScript · Vite |

## Quick start

```bash
make install
cp .env.example .env
make dev
```

Backend on `:8000`, frontend on `:5173`. Health check: `curl localhost:8000/api/health`.

The default `LLM_PROVIDER=replay` needs **no API keys** — it replays recorded LLM responses
from the DuckDB catalog, so the full loop runs offline.

## Commands

| Command | Does |
|---|---|
| `make install` | Sync Python deps (uv) and npm packages |
| `make dev` | Run backend and frontend together |
| `make api` / `make web` | Run one side only |
| `make test` | pytest |
| `make lint` | ruff + tsc |
| `make clean` | Drop the local catalog, artifacts, and build output |

## Layout

```
backend/app/
  ingest/     read csv/excel/json as all-strings, add _src_row
  profile/    per-column stats, format pattern mining, semantic detection
  target/     canonical schema YAML -> Pandera schema
  propose/    heuristics + embeddings + LLM, ensembled with provenance
  transform/  MappingSpec -> DuckDB SQL and Polars Python
  validate/   Pandera run -> plain-English rejection report
schemas/      canonical target schemas
samples/      deliberately messy fixtures
```

## Status

Phase 1 of 7 — skeleton. Backend, catalog, and frontend shell are up.
See the build phases in the project plan for what lands next.
