.PHONY: test test-cov test-core-cov lint format build clean

PYTHON ?= .venv/bin/python

test:
	$(PYTHON) -m pytest tests/ -v --tb=short

test-cov:
	$(PYTHON) -m pytest tests/ -v --cov=axc_agent_engine --cov-report=html

test-core-cov:
	$(PYTHON) -m pytest tests/core tests/engine tests/runtime tests/tools tests/storage tests/workflow tests/planning \
		--cov=axc_agent_engine.agent \
		--cov=axc_agent_engine.engine \
		--cov=axc_agent_engine.core \
		--cov=axc_agent_engine.runtime \
		--cov=axc_agent_engine.tools \
		--cov=axc_agent_engine.storage \
		--cov=axc_agent_engine.workflow \
		--cov=axc_agent_engine.planning \
		--cov-report=term-missing:skip-covered \
		--cov-fail-under=98 -q

lint:
	ruff check axc_agent_engine/

format:
	ruff format axc_agent_engine/

build:
	$(PYTHON) -m build

clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache htmlcov coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
