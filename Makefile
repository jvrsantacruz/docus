.PHONY: lint test

lint:
	uv run ruff check docus.py tests/
	uv run vulture docus.py --min-confidence 80
	uv run ty check docus.py
	uv run xenon docus.py --max-absolute F --max-modules D --max-average B

test:
	uv run pytest
