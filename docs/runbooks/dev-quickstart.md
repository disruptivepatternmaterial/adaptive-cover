# Developer Quickstart

## 1) Setup

```bash
scripts/setup
```

This installs `requirements-dev.txt` and installs pre-commit hooks.

## 2) Local validation

```bash
scripts/check
```

`scripts/check` runs the standard local gate (lint + tests).

It also validates release metadata consistency.

If you need to run only metadata checks:

```bash
python3 scripts/validate_release_metadata.py
```

## 3) Optional local HA runtime

```bash
scripts/develop
```

This runs Home Assistant against the local `config/` directory with `custom_components` on `PYTHONPATH`.

## 4) Contribution expectations

- Keep commits focused and reviewable.
- Add regression tests for bug fixes.
- Update `README.md` and `CHANGELOG.md` when behavior changes.
- Prefer explicit evidence in PR descriptions (commands run + outcomes).
