.PHONY: lint test ci

lint:
	.venv/bin/ruff check src tests
	.venv/bin/python scripts/check_no_personal.py

test:
	.venv/bin/python -m pytest tests/ -q

ci: lint test
