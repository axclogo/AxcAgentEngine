# Contributing to AxcAgentEngine

## Development Setup

```bash
git clone https://github.com/axcteam/axc-agent-engine.git
cd axc-agent-engine
pip install -e ".[dev]"
pre-commit install
python -m pytest
```

## Code Style

- Python 3.11+, full type hints
- Prefer small functions and explicit module boundaries.
- All network, storage, and model IO should be async.
- Keep public API changes narrow and documented.
- Run `ruff check`, `ruff format --check`, `mypy`, and `pytest` before committing.
- Use `pre-commit run --all-files` before opening a pull request when practical.

Recommended local validation:

```bash
python -m pytest
python -m compileall -q axc_agent_engine
ruff check axc_agent_engine tests
ruff format --check axc_agent_engine tests
python -m mypy axc_agent_engine
python -m build
twine check dist/*
```

## Pull Request Process

1. Fork the repo and create a branch from `main`
2. Add tests for any new functionality
3. Ensure all tests pass: `python -m pytest tests/ -v`
4. Ensure lint passes: `ruff check axc_agent_engine tests`
5. Update docs for public behavior changes
6. Submit a PR with a clear description

## Plugin Development

See `docs/PLUGIN_DEVELOPMENT.md` and `examples/05_custom_plugin/`.

All plugins implement the same `Plugin` Protocol defined in `axc_agent_engine/plugins/__init__.py`.

## API Compatibility

The OpenAI-compatible HTTP API is a documented subset. Any change to request
parameters, stream chunks, error shape, or `/v1/capabilities` must update
`docs/API.md` and `tests/test_api_chat.py`.

## Security-Sensitive Changes

Changes touching tools, command execution, HTTP requests, MCP, plugin loading,
workspace validation, memory, or persistence should include tests and should be
reviewed against `SECURITY.md` and `docs/SECURITY_MODEL.md`.

## Reporting Issues

Use GitHub Issues. Include:
- Python version
- AxcAgentEngine version
- Minimal reproduction steps
- Expected vs actual behavior
