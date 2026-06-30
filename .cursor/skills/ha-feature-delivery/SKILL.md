---
name: ha-feature-delivery
description: Deliver adaptive_cover features end-to-end with execution-first flow, HA hygiene, tests, docs updates, and local validation evidence. Use when implementing or refactoring integration behavior.
disable-model-invocation: true
---

# HA Feature Delivery

Use this workflow when implementing or hardening `adaptive_cover`.

## Workflow

1. Read relevant runtime and docs files first (`custom_components/adaptive_cover/**`, `README.md`, `CHANGELOG.md`).
2. Implement the smallest correct change set.
3. Add/adjust regression tests under `tests/`.
4. Update docs when behavior or configuration semantics change.
5. Run validation:
   - `scripts/check`
   - `python3 scripts/validate_release_metadata.py` (if version/release/docs metadata changed)
6. Summarize:
   - what changed
   - exact commands run and outcomes
   - residual risks or follow-ups

## Required checks

- Preserve backward compatibility for existing options keys.
- Guard coordinator/entity behavior against unavailable HA state.
- Keep diagnostics output redacted.
- Avoid introducing undeclared runtime dependencies.

## Output checklist

Copy and complete:

```text
Task Progress:
- [ ] Runtime changes implemented
- [ ] Regression tests added/updated
- [ ] Docs updated
- [ ] scripts/check passed
- [ ] metadata validation passed (if needed)
- [ ] Results and residual risk summarized
```
