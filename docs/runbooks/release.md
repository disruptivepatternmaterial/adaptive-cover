# Release Runbook

This repository uses `vX.Y.Z` tags for releases.

## Preflight

1. Confirm clean working tree on target branch.
2. Ensure these files agree on release version:
   - `custom_components/adaptive_cover/manifest.json`
   - `pyproject.toml`
   - `README.md` current release reference
   - `CHANGELOG.md` release heading
3. Run:

```bash
scripts/check
python3 scripts/validate_release_metadata.py
```

## Publish

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

## Verify

1. Remote tag exists.
2. Release page exists.
3. Confirm release notes match `CHANGELOG.md` and intended scope.
4. Local branch tracks cleanly against remote.

## Recovery

- If tag is wrong and not consumed: delete local + remote tag, fix metadata, retag.
- If release exists with wrong notes: edit release body and keep tag immutable.
