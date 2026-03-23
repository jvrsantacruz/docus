.PHONY: lint test test-unit test-functional

lint:
	uv run ruff check docus.py tests/
	uv run vulture docus.py --min-confidence 80
	uv run ty check docus.py
	uv run xenon docus.py --max-absolute F --max-modules D --max-average B

test: test-unit test-functional

test-unit:
	uv run pytest tests/test_unit.py

test-functional:
	uv run pytest tests/test_functional.py -v --override-ini="addopts="
