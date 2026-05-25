.PHONY: test lint format build clean

test:
	python -m pytest tests/ -v --tb=short

test-cov:
	python -m pytest tests/ -v --cov=axc_agent_engine --cov-report=html

lint:
	ruff check axc_agent_engine/

format:
	ruff format axc_agent_engine/

build:
	python -m build

clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache htmlcov coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
