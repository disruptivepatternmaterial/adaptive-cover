# Release Runbook

This repository uses `vX.Y.Z` tags for releases.

## Preflight

0. Remote sync check (mandatory):

```bash
git fetch origin
git status -sb            # must be up to date with origin/main, not diverged
git ls-remote --tags origin | grep <target-version>   # must be empty
```

If local and origin have diverged, reconcile first — never tag from a stale
base (the 2026-07-02 v0.3.8/v0.3.9 collision shipped two parallel fix lines).

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
python3 scripts/validate_runtime_deps.py
```

`scripts/check` runs both validators, so this is belt-and-braces for a release
cut by hand. `validate_runtime_deps.py` covers three things: every third-party
import is declared in `manifest.json`, the Home Assistant floor agrees across
`hacs.json` / `requirements.txt` / `pyproject.toml`, and the integration does no
blocking file I/O in the event loop.

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
