# Recorded LLM responses

`LLM_PROVIDER=replay` (the default) serves the files in this directory instead of calling a
model, which is what lets the full loop run on a fresh clone with no API key and no network.

**This directory ships empty.** Recording requires a real API key, so the responses are not
committed by anyone who does not have one. Until you record them, the proposer runs with the
heuristic and embedding voices only and reports the LLM as unavailable — it degrades, it does
not fail.

To record:

```bash
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... uv run python scripts/record_llm.py
```

Each file is named for the hash of its request, computed from the column profiles and the
target schema. Changing either produces a different hash and a fixture miss, which is
deliberate: a stale recording is worse than a reported absence.
