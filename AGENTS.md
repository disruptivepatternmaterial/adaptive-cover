# Adaptive Cover Agent Contract

This repository follows an execution-first workflow for both humans and AI agents.

## Core behavior

- Implement requested work directly unless a real product decision is blocked.
- Prefer small, focused changes over large mixed edits.
- Keep `main` release-ready after each merged change.

## Definition of done

For any behavior, config, workflow, or release change:

1. Add or update tests (regression-first for bug fixes).
2. Run validation gates locally:
   - `scripts/check`
   - `python3 scripts/validate_release_metadata.py`
3. Update docs that users rely on (`README.md`, `CHANGELOG.md`, runbooks/specs as needed).
4. Summarize what changed, what was validated, and remaining risks.

## Home Assistant hygiene requirements

- Treat runtime imports as production contracts; do not rely on transitive deps.
- Keep config-flow defaults and option migrations backward-compatible.
- Redact entity identifiers in diagnostics output.
- Prefer deterministic coordinator behavior under startup, unavailable entities, and reloads.

## Release discipline

- Keep release metadata synchronized:
  - `custom_components/adaptive_cover/manifest.json`
  - `pyproject.toml`
  - `README.md` current release reference
  - `CHANGELOG.md` heading for the release
- Tag format: `vX.Y.Z` for this fork (`0.3.x` line at present).
- Follow `docs/runbooks/release.md` exactly.

## Review posture

- Run adversarial review before significant merges.
- Prioritize correctness, regressions, security, and maintainability over style-only feedback.
- Convert accepted findings into concrete issues or immediate fixes.
