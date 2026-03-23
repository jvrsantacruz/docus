.PHONY: lint test

lint:
	uv run ruff check docus.py tests/

test:
	uv run pytest
