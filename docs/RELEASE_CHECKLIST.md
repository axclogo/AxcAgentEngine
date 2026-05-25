# Release Checklist

Use this before publishing a public release.

## Version

- [ ] Update `pyproject.toml` version.
- [ ] Update `CHANGELOG.md`.
- [ ] Tag matches package version: `vX.Y.Z`.

## Validation

- [ ] `python -m pytest`
- [ ] `python -m compileall -q axc_agent_engine`
- [ ] `ruff check axc_agent_engine tests` after recreating the local env
- [ ] `ruff format --check axc_agent_engine tests` after the formatting baseline is established
- [ ] `python -m mypy axc_agent_engine` after recreating the local env
- [ ] `python -m build`
- [ ] `twine check dist/*`
- [ ] `pre-commit run --all-files` after the formatting baseline is established

## Packaging

- [ ] `LICENSE` included.
- [ ] `NOTICE` included.
- [ ] `README.md` renders on PyPI.
- [ ] `py.typed` included.
- [ ] Examples do not include secrets.
- [ ] No local cache/log files are included.
- [ ] `.axc/`, virtual environments, caches, and local data directories are ignored.

## Security

- [ ] `SECURITY.md` reviewed.
- [ ] High-risk tool defaults reviewed.
- [ ] Dependency audit reviewed.
- [ ] Secret scan reviewed.
- [ ] GitHub security workflows pass.
- [ ] OpenAI API compatibility docs updated if API changed.
