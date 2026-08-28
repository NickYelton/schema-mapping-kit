# Schema Mapping & Onboarding Kit

A customer hands you a spreadsheet full of garbage and you have to get it into your canonical
schema by Friday.

This tool ingests an arbitrary CSV/Excel/JSON file, profiles every column, proposes a mapping
to a target schema using embeddings + an LLM + dumb-but-effective heuristics, presents it in a
review UI where a human confirms or corrects, and emits a **versioned, deterministic transform**
(SQL or Python) that runs forever after without touching the LLM. Failed rows get a plain-English
rejection report the customer can actually act on.

> **The LLM is a design-time assistant, never a runtime dependency.**
> It proposes. A human disposes. The compiled output is pure SQL/Polars.

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

## Sample files

Four fixtures in `samples/`, each encoding a distinct class of mess. Regenerate with
`uv run python samples/generate.py`.

| File | What's wrong with it |
|---|---|
| `orders_clean.csv` | Nothing — the control |
| `orders_messy.csv` | 4-row preamble, cp1252, `;` delimiter, `$1,142.00`, five null spellings, mixed-case status, a column named `X7`, and scattered unparseable values |
| `orders_euro.xlsx` | 3 sheets, merged two-row header, `DD/MM/YYYY`, `1.234,56` decimals |
| `orders_nested.jsonl` | Nested objects, `quantity` vs `qty` key drift, missing fields |

## Status

Phase 2 of 7 complete — ingest and profiling.

Working now: land CSV/Excel/JSON as all-strings with a `_src_row` traceable to the original
file, sniff encoding/delimiter/preamble, flatten merged Excel headers and nested JSON, then
profile every column (types, cardinality, null rate, format patterns, semantic tags) and
browse the result in the UI.

Next: the canonical target schema and Pandera generation (Phase 3).
