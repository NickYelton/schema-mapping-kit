.PHONY: install dev api web test lint fmt clean fetch-model record-llm

install:
	uv sync
	cd frontend && npm install

api:
	uv run uvicorn app.main:app --app-dir backend --reload --port 8000

# One-time ~260MB download. The embedding proposer resolves the model from the local
# HuggingFace cache only, so without this it reports itself unavailable rather than
# blocking a request on a download.
fetch-model:
	uv run python -c "from huggingface_hub import snapshot_download; \
	  import os; n=os.environ.get('EMBEDDING_MODEL','minishlab/potion-retrieval-32M'); \
	  print('fetching', n); print('cached at', snapshot_download(n))"

# Record LLM responses for the bundled samples so LLM_PROVIDER=replay can serve them.
# Needs a real key: LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... make record-llm
record-llm:
	uv run python scripts/record_llm.py

web:
	cd frontend && npm run dev

# Runs both servers; Ctrl-C stops the pair.
dev:
	@trap 'kill 0' EXIT INT TERM; \
	$(MAKE) api & \
	$(MAKE) web & \
	wait

test:
	uv run pytest -q

lint:
	uv run ruff check backend
	cd frontend && npx tsc --noEmit

fmt:
	uv run ruff format backend
	uv run ruff check --fix backend

clean:
	rm -rf data/*.duckdb data/*.duckdb.wal artifacts/* frontend/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
