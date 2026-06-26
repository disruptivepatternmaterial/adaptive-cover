# Changelog — Adaptive Cover (NET Fork)

All notable changes to this fork are documented here. This fork uses `0.3.x` versioning. Upstream (`basbruss/adaptive-cover`) uses `1.x.x` versioning — do not confuse the two.

---

## [0.3.7] — 2026-06-25

Lower-priority bug fixes from a 4-model adversarial code review of v0.3.6.

### Fixed

**`coordinator.py` — `async_timed_refresh`**
- Midnight end-times (00:00) now fire correctly. Previously the raw string was compared without applying the `+1 day` rollover that `_end_time` uses, causing midnight end-times to always be treated as past and never trigger.
- Trigger window changed from strict `<= 1s` to `abs(delta) <= 60s` so event-loop pressure no longer causes the callback to silently skip.
- Added `None` guard on `get_datetime_from_str` result — unparseable end-time entity states now return early instead of crashing.

**`coordinator.py` — `climate_mode_data`**
- Changed two independent `if` blocks to `if/elif` so `"winter"` cannot silently override `"summer"` when both are simultaneously `True` (possible when `temp_high < temp_low` is misconfigured).
- Logs a `warning` when both `is_summer` and `is_winter` are `True` so the user knows their thresholds are inverted.

**`calculation.py` — `solar_times()`**
- Snapshots `sun_data.times` once and drives both azimuth, elevation, and `set_index` from the same `DatetimeIndex`. Previously three separate `times` calls could yield misaligned arrays if a midnight boundary was crossed mid-call.

**`sun.py` — `solar_azimuth` / `solar_elevation`**
- Simplified now that `solar_times()` owns the shared snapshot.
- Removed an inaccurate comment claiming a local variable assignment was an optimization (it was a no-op — Python evaluates a `for ... in <expr>` iterable exactly once).

**`helpers.py` — `get_datetime_from_str`**
- Now returns `None` instead of raising `ValueError`/`OverflowError` when a non-datetime sensor state is configured for start/end time entities. All callers already guard against `None` before arithmetic.

---

## [0.3.6] — 2026-06-25

Full code review pass. 22 fixes across all source files — no new features.

### Fixed — Crashes

| File | Bug | Fix |
|------|-----|-----|
| `coordinator.py` | `get_blind_data` used three independent `if` blocks — unknown cover type raised `UnboundLocalError` | Changed to `if/elif/else` with explicit `ValueError` |
| `coordinator.py` | `async_timed_refresh` left `time` unbound when both `end_time` and `end_time_entity` were `None` | Initialize to `None`; added early return |
| `coordinator.py` | `async_check_cover_state_change` had no guard for `new_state=None` (fires when cover entity removed from HA) | Added early return |
| `coordinator.py` | `handle_state_change` called `abs(target - new_position)` / `abs(our_state - new_position)` without checking `new_position` for `None` (mid-travel or unavailable cover) | Guarded both arithmetic sites; added early return |
| `coordinator.py` | `after_start_time` entity path crashed with `TypeError` (`now >= None`) when start-time entity was unavailable | Added `None` guard before datetime comparison; returns `False` on unavailable entity |
| `coordinator.py` | `async_timed_refresh`: entity unavailability overwrote the valid config fallback with `None`, skipping the timed refresh | Entity value only applied when non-`None`; static config retained as fallback |
| `calculation.py` | `lux` / `irradiance` called `float(None)` when sensor unavailable | Added `None` guard + `try/except (TypeError, ValueError)` |
| `calculation.py` | `get_current_temperature` called `float()` unguarded on inside and outside temperature values | Wrapped both paths in `try/except` |
| `switch.py` | `len(config_entry.options.get(CONF_ENTITIES))` raised `TypeError` when key absent | Added `[]` default |

### Fixed — Logic / Behavioral

| File | Bug | Fix |
|------|-----|-----|
| `coordinator.py` | `after_start_time` — `self._start_time` was a dead no-op expression instead of the intended assignment | Assignment restored |
| `coordinator.py` | `control_method` never reset between update cycles — a summer→neutral transition left the sensor permanently showing `"summer"` | Reset to `"intermediate"` at the start of each `climate_mode_data()` call |
| `coordinator.py` | `climate_mode_data` constructed `ClimateCoverState` twice per update cycle, running the full decision tree twice | Reuse single instance |
| `coordinator.py` | `button.py` — on 30s timeout, `wait_for_target` and `target_call` left dirty, permanently suppressing manual-override detection | Clear both dicts for the entity on timeout |
| `calculation.py` | `outside_high` returned `True` when sensor unavailable (biasing `is_summer` during outages) AND returned `False` when no outdoor threshold configured (breaking summer mode for users without outdoor sensor) | Split into two cases: `temp_summer_outside is None` → `True` (no gate configured); `outside_temperature is None` → `False` (gate configured, sensor offline) |
| `__init__.py` | `CONF_START_ENTITY` missing from state-change listener list — dynamic start-time entity changes never triggered a refresh | Added alongside `CONF_END_ENTITY` |

### Fixed — Quality

| File | Fix |
|------|-----|
| `__init__.py` | Removed dead `async_initialize_integration` function (never called anywhere) |
| `button.py` | Unbounded busy-wait loop capped at 30s with `warning` log |
| `calculation.py` | `datetime.utcnow()` deprecated in Python 3.12 — replaced with `datetime.now(_dt.UTC)` |
| `helpers.py` | `ignoretz=True` silently discarded timezone info from HA `input_datetime` strings — now converts aware strings to local naive datetime |
| `coordinator.py` | `check_position` log message incorrectly said "Cover is already at position" when the entity was actually unavailable — corrected |

---

## [0.3.0b2] — 2026-06-24

- **README:** full NET Fork install/deploy docs, changelog, tests, corrected HACS/repo URLs and badges

---

## [0.3.0b1] — 2026-06-24

- **Manual override persistence:** `manual_control` and `manual_control_time` stored in HA `Store` (`adaptive_cover.{entry_id}.manual_state`); restored before first coordinator refresh after HA restart. Fixes shades reopening to calculated positions after reboot when they were manually held.
- **Startup guard:** cover position drives deferred until all switch entities report restored state (`expected_restore_ids` / `mark_switch_restored` on coordinator).
- **HACS/manifest:** renamed to **Adaptive Cover (NET Fork)**; docs and issue tracker point at this repo.
- **Tests:** `tests/test_coordinator_manual_persist.py` (13 tests covering Store load/save, malformed timestamps, switch-restore gate).

---

## Prior NET Fork commits

- **Window-open latch:** `_last_window_open_ts` holds covers at max open for `window_open_hold` seconds after a contact sensor closes, guarding against flaky sensors.
- **Winter overrides anti-glare** in `normal_with_presence` climate path — cold weather always opens covers for solar gain regardless of brightness sensors.
- **Multi-window sensors:** `window_entity` accepts a list; separate `cloud_coverage_entity` source for WeatherFlow and similar.
- **Config flow:** legacy single-string `window_entity` coerced to list for multi-select UI.
- **Forecast-based summer detection:** uses `weather.get_forecasts` service (HA 2024.4+ API) to predict peak heat before it arrives.
- **Cloud coverage deadband:** 35–65% deadband prevents thrashing; dedicated `cloud_coverage_entity` takes priority over weather entity attribute.
