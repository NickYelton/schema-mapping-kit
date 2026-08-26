.PHONY: install dev api web test lint fmt clean

install:
	uv sync
	cd frontend && npm install

api:
	uv run uvicorn app.main:app --app-dir backend --reload --port 8000

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
