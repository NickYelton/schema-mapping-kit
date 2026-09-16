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
| `make fetch-model` | Download the embedding model into the local HF cache (one-time) |
| `make record-llm` | Record LLM responses for the samples so `replay` can serve them |
| `make clean` | Drop the local catalog, artifacts, and build output |

## Layout

```
backend/app/
  ingest/     read csv/excel/json as all-strings, add _src_row
  profile/    per-column stats, format pattern mining, semantic detection
  models/     pydantic models shared across phases (target schema, mapping spec)
  target/     canonical schema YAML -> Pandera schema
  propose/    heuristics + embeddings + LLM, ensembled with provenance
              providers/  one file per voice; each degrades independently
  transform/  MappingSpec -> DuckDB SQL and Polars Python
              pipeline.py shared op contract; sql.py and polars_engine.py compile it
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

All 7 phases complete — from a messy file to a reviewed, versioned, deterministic transform.

Working now: land CSV/Excel/JSON as all-strings with a `_src_row` traceable to the original
file, sniff encoding/delimiter/preamble, flatten merged Excel headers and nested JSON, then
profile every column (types, cardinality, null rate, format patterns, semantic tags) and
browse the result in the UI. Alongside it, `schemas/orders_v1.yaml` defines the canonical
target — dtypes, nullability, constraints, and a composite primary key — which compiles to a
Pandera schema that rejects bad enums, out-of-range numbers, malformed ids, and duplicate
keys, and is browsable at `/api/schemas`.

The proposer ensembles three voices into a versioned `MappingSpec`, each mapping carrying the
evidence that produced it. Measured against all four fixtures — 50 columns with a known
correct answer — the name-and-profile heuristic maps 50/50 and the embedding provider 40/50.
The heuristic therefore leads the ensemble and is the only voice always present, which makes
it the offline floor for the whole tool.

A reviewed `MappingSpec` compiles to a **DuckDB SELECT** and a **standalone Polars module**,
both written to `artifacts/transforms/`. Every op is string-to-string and `cast` is the single
typed boundary that ends each pipeline, which is what keeps the two compilers symmetric and
makes any op composable with any other. Casts never raise: an unconvertible value becomes
null and phase 6 will explain it against the original row.

The load-bearing test is three-way determinism — the SQL, the Polars expressions, and the
rendered Python module must produce byte-identical frames on all four fixtures. Once that
holds, the transform a customer runs forever after is the one that was reviewed.

Every run validates its output against the phase 3 Pandera schema. Rows that pass become the
output; rows that fail are explained in terms of the customer's own file — the line number,
their column name, and the value they actually sent — because `_src_row` carries the original
line through every step. A `NULL` or `n/a` is reported as empty, `13/45/2024` as an unreadable
date in the expected format, and identical failures are grouped, so thirty untranslated German
statuses read as five problems rather than thirty. Each run writes `run_<id>_report.json` and a
`run_<id>_rejections.csv` that opens cleanly in Excel. A malformed transform — a wrong dtype, a
missing column — is a structural error, not a row rejection.

The review UI closes the loop. Load a file, open **Review & run**, and propose a mapping.
Every target field shows the column feeding it, the proposer's confidence and evidence, an
editable pipeline of transform steps, and before/after samples. Each edit is checked against
the whole file without being saved, so the pass meter and the rows that would be rejected
update as you work — an unaccepted value such as `retourniert` can be mapped to its canonical
status right from the rejection list. Saving creates a new content-addressed version with a
record of what changed, and a run compiles and validates the saved version. Runs are blocked
while edits are unsaved, so what runs is always a version someone saved.

### Optional providers

Both optional voices degrade rather than fail, so a fresh clone works with neither:

| Provider | Needs | Without it |
|---|---|---|
| embeddings | `make fetch-model` (~260MB, one-time) | reported unavailable, ensemble runs on the rest |
| LLM | `make record-llm` with an API key | `replay` reports no recordings, ensemble runs on the rest |

`fixtures/llm/` ships **empty** — recordings need a real key, so they are not committed by
anyone who lacks one. Record them once and commit them, and `LLM_PROVIDER=replay` serves the
full ensemble offline forever after.
