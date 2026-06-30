# Contribution Guidelines

Thanks for contributing to Adaptive Cover (NET Fork).

## Workflow

1. Branch from `main`.
2. Make focused changes (avoid mixing unrelated runtime/tests/docs changes).
3. Run setup once:
   - `scripts/setup`
4. Run validation before opening a PR:
   - `scripts/check`
   - `python3 scripts/validate_release_metadata.py` (when version/release/docs metadata changed)
   - (`scripts/check` requires dependencies installed via `scripts/setup`)
5. Update docs when behavior/config changes:
   - `README.md`
   - `CHANGELOG.md`
   - specs/runbooks under `docs/` when contracts or process changed

## Pull request expectations

- Include a clear summary of what changed and why.
- Include command output for lint/tests/checks.
- Add or update regression tests for bug fixes.
- Flag breaking behavior explicitly.

## Local development helpers

- `scripts/setup` installs dev dependencies and pre-commit hooks.
- `scripts/check` runs lint + tests + metadata checks.
- `scripts/develop` starts a local Home Assistant instance using `config/`.

See:
- `docs/runbooks/dev-quickstart.md`
- `docs/runbooks/release.md`
- `docs/specs/behavioral-contract.md`
- `docs/specs/config-flow-contract.md`

## Reporting issues

Use GitHub Issues with clear repro steps, expected behavior, actual behavior, and diagnostics when available.

## License

By contributing, you agree that your contributions are licensed under the MIT License.
