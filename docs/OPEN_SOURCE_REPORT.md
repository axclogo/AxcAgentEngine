# Open Source Readiness Report

Date: 2026-05-18

## Summary

AxcAgentEngine is close to open-source ready. The project now includes core
governance files, CI/release workflows, examples, API compatibility
documentation, security guidance, plugin development guidance, and a release
checklist.

## Completed

- Apache-2.0 `LICENSE` present.
- Added `NOTICE`.
- Added `SECURITY.md`.
- Added `CODE_OF_CONDUCT.md`.
- Added `CHANGELOG.md`.
- Added GitHub issue templates.
- Added pull request template.
- Fixed CI and Dependabot paths for the repository root.
- Added OpenAI API compatibility documentation.
- Added plugin development documentation.
- Added security model documentation.
- Added release checklist.
- Added examples index.
- Added pre-commit configuration.
- Added GitHub security workflow with CodeQL, Gitleaks, and OSSF Scorecard.
- Removed tracked local `.axc` execution logs.
- Ignored local `.axc`, env, cache, and data artifacts.
- Synchronized Chinese README and `llms.txt` with the current open-source/API posture.
- README links to open-source governance and docs.
- API layer exposes `/v1/capabilities`.
- Full test suite passes locally.

## Current Validation

- `pytest -q`: passing.
- `python3 -m compileall -q axc_agent_engine`: passing.
- `git diff --check`: passing.
- `.github` and example YAML parsing: passing.
- Secret/local-path scan should be run before final tag.

## Static Analysis Status

The repository has a broad historical formatting footprint. Running ruff format
against the full tree currently proposes formatting many existing files. Do not
enable ruff format as a required CI gate until the repository performs a
dedicated formatting-only change.

The previously committed local `.venv` points at an old path and should not be
used as a release signal. Recreate a local environment before running static
analysis:

```bash
pip install -e ".[dev]"
ruff check axc_agent_engine tests
ruff format --check axc_agent_engine tests
python -m mypy axc_agent_engine
```

## Remaining Recommendations

These are not blockers for initial open source release, but they should be
tracked:

- Add production storage adapters or publish adapter recipes.
- Continue reducing `Any` and bare `dict` at message/tool/API boundaries.
- Split large built-in plugins into smaller service/tool adapter modules.
- Add API contract tests using a real OpenAI SDK client if full client
  compatibility becomes a goal.
- Configure the real GitHub repository URL and PyPI Trusted Publisher before
  cutting the first public tag.
- Publish security hardening examples for sandboxed command execution.

## Release Gate

Before tagging a public release, follow `docs/RELEASE_CHECKLIST.md`.
