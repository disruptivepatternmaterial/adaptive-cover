---
name: ha-release-operator
description: Execute adaptive_cover releases safely with metadata sync, validation gates, tagging, push, and release verification. Use when preparing or publishing a version.
disable-model-invocation: true
---

# HA Release Operator

Use this skill for `vX.Y.Z` release preparation and publication.

## Preflight

1. Confirm clean working tree and expected branch.
2. Verify release metadata is synchronized:
   - `custom_components/adaptive_cover/manifest.json`
   - `pyproject.toml`
   - `README.md` current release line
   - `CHANGELOG.md` release section
3. Run:
   - `scripts/check`
   - `python3 scripts/validate_release_metadata.py`

## Publish flow

1. Create focused commits (runtime/tests/docs as needed).
2. Tag release: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`.
3. Push branch and tag.
4. Verify:
   - remote tag exists
   - release exists in GitHub
   - expected assets are present

## Post-release verification checklist

```text
- [ ] Branch and tag pushed
- [ ] GitHub release page exists
- [ ] Release notes look correct
- [ ] Asset(s) published (if workflow attaches ZIP)
- [ ] Local tree clean
```
