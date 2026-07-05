.PHONY: install playground run test lint

install:
	uv sync

playground:
	uv run adk web app --host 127.0.0.1 --port 8080 --reload_agents

run:
	uv run adk web app --host 127.0.0.1 --port 8080 --reload_agents

test:
	uv run pytest tests/unit tests/integration

lint:
	uv run ruff check .
