# Config Flow Contract — Adaptive Cover

This contract documents compatibility expectations for config and options flow evolution.

## Compatibility rules

- Keep existing option keys stable unless a migration path is added.
- New options must define safe defaults for existing entries.
- Legacy single-value fields that evolved to lists must be normalized on read.
- Unique IDs must be deterministic and normalized from user-visible names.
- Invalid user input should return translated errors (not silent failures).

## Critical keys

| Key | Scope | Expectation |
|---|---|---|
| `mode` | create options | Persisted and aligned with selected sensor type. |
| `manual_override_duration` | create/options | Supports legacy sentinel normalization and safe runtime defaults. |
| `window_entity` | create/options | Accepts list, normalizes legacy string input. |
| `window_open_hold` | create/options | Stored in seconds and validated as numeric. |
| `start_time` / `end_time` | automation | Handles static values and entity-driven values safely. |
| `return_sunset` | automation | Stable boolean semantics with existing entries. |

## Required updates when flow changes

1. Update this spec when adding/removing/changing option semantics.
2. Add regression tests in `tests/test_config_flow_hardening.py`.
3. Update translation keys (`custom_components/adaptive_cover/translations/*.json`).
4. Update user docs (`README.md`) when option behavior changes.
