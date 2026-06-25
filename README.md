![Version](https://img.shields.io/github/v/release/disruptivepatternmaterial/adaptive-cover?style=for-the-badge)

![logo](images/logo.png#gh-light-mode-only)
![logo](images/dark_logo.png#gh-dark-mode-only)

# Adaptive Cover (NET Fork)

Sun-tracking cover control for Home Assistant: vertical blinds, awnings, and venetian tilts with optional climate-aware strategies.

**This repo:** [disruptivepatternmaterial/adaptive-cover](https://github.com/disruptivepatternmaterial/adaptive-cover)  
**Current release:** [v0.3.0b3](https://github.com/disruptivepatternmaterial/adaptive-cover/releases/tag/v0.3.0b3)  
**HACS name:** `Adaptive Cover (NET Fork)`  
**Integration domain:** `adaptive_cover`

Fork lineage: [basbruss/adaptive-cover](https://github.com/basbruss/adaptive-cover) → [rako79/adaptive-cover](https://github.com/rako79/adaptive-cover) → this fork.

Based on the community template approach: [Automatic Blinds](https://community.home-assistant.io/t/automatic-blinds-sunscreen-control-based-on-sun-platform/).

---

## Install (HACS)

1. HACS → **Integrations** → **⋮** → **Custom repositories**
2. Add `https://github.com/disruptivepatternmaterial/adaptive-cover` as type **Integration**
3. Search **Adaptive Cover (NET Fork)** → **Download**
4. Restart Home Assistant
5. **Settings → Devices & services → Add integration** → **Adaptive Cover (NET Fork)**

Do **not** add `basbruss/adaptive-cover` — that is upstream and a different release line (v1.4.x). This fork uses **0.3.0b*** versioning.

After adding the custom repo once, updates: HACS → **Adaptive Cover (NET Fork)** → **Update** (should show **v0.3.0b3** when [GitHub release](https://github.com/disruptivepatternmaterial/adaptive-cover/releases) exists).

### Manual install

Copy `custom_components/adaptive_cover/` to `/config/custom_components/` and restart HA.

---

## Deploy checklist (BowmanMtn)

| Step | Command / action |
|------|------------------|
| Pull latest | HACS → Update **Adaptive Cover (NET Fork)** |
| Verify version | `/config/custom_components/adaptive_cover/manifest.json` → `"version": "0.3.0b3"` |
| Restart | Restart Home Assistant |
| Smoke test | Manually hold a shade closed → restart HA → shade should **not** reopen on first refresh |

---

## NET Fork changes (changelog)

### v0.3.0b3

**Bug fixes (17 issues resolved)**

- **Crash — unknown cover type:** `get_blind_data` used three independent `if` statements; an unrecognised `sensor_type` value raised `UnboundLocalError`. Changed to `if/elif/else` with an explicit `ValueError`.
- **Crash — timed refresh:** `async_timed_refresh` left the `time` variable unbound when both `end_time` and `end_time_entity` were `None`, crashing immediately. Now initialised to `None` with an early return.
- **Crash — cover entity removed:** `async_check_cover_state_change` had no guard for `new_state=None` (HA fires this when a cover entity is deleted while the integration is running). Added early return.
- **Crash — cover mid-travel:** `handle_state_change` called `abs(target - new_position)` without checking `new_position` for `None` (happens while cover is moving or entity is unavailable). Both arithmetic sites guarded.
- **Crash — unavailable lux/irradiance sensor:** `lux` and `irradiance` properties called `float(value)` on the result of `get_safe_state`, which returns `None` for unavailable entities. Added `None` check and `try/except`.
- **Crash — no covers configured:** `switch.py` called `len(config_entry.options.get(CONF_ENTITIES))` without a default; raises `TypeError` when the key is absent. Added `[]` default.
- **Silent bug — `_start_time` not stored:** `after_start_time` had a dead expression `self._start_time` (no-op read) instead of `self._start_time = time`. Assignment restored.
- **Logic bug — `control_method` stuck on season:** `control_method` was never reset between update cycles; a summer→neither-season transition left the Control Method sensor permanently showing `"summer"`. Now reset to `"intermediate"` at the start of each cycle.
- **Logic bug — `ClimateCoverState` constructed twice:** `climate_mode_data` instantiated `ClimateCoverState` twice, running the full decision tree twice per update. First instance is now reused.
- **Missing listener — dynamic start time:** `CONF_START_ENTITY` was absent from the state-change listener list in `__init__.py`; changes to a dynamic start-time entity never triggered a coordinator refresh.
- **Dead code — `async_initialize_integration`:** function was never called anywhere; removed.
- **Hang — Reset button:** `async_press` had an unbounded `while` loop waiting for cover position confirmation; if the cover never responded the handler hung forever. Capped at 30 s with a warning log.
- **Deprecated — `datetime.utcnow()`:** replaced with `datetime.now(dt.UTC)` throughout (deprecated since Python 3.12, the project's target version).
- **Wrong fail-safe — `outside_high`:** returned `True` (assume "hot outside") when the outdoor temperature sensor was unavailable, biasing `is_summer` to `True` during outages. Now returns `False`.
- **Wrong fail-safe — `get_datetime_from_str`:** `ignoretz=True` silently discarded timezone information from HA `input_datetime` strings, producing off-by-offset errors in non-UTC environments. Now converts timezone-aware strings to local naive datetimes.
- **Dead property — `_get_azimuth_edges`:** had a wrong return-type annotation (`tuple[int,int]` but returned `int`) and was referenced nowhere. Removed.
- **Performance — `SunData` date range recomputed per loop iteration:** `solar_azimuth` and `solar_elevation` called `self.times` (a property that rebuilds the full `pd.date_range`) on every loop step. Cached to a local variable.

### v0.3.0b2

- **README:** full NET Fork install/deploy docs, changelog, tests, corrected HACS/repo URLs and badges

### v0.3.0b1

- **Manual override persistence:** `manual_control` and `manual_control_time` stored in HA `Store` (`adaptive_cover.{entry_id}.manual_state`); restored before first coordinator refresh after HA restart  
  - Fixes shades reopening to calculated night positions after reboot when they were manually held closed
- **Startup guard:** cover position drives deferred until **all** switch entities report restored state (`expected_restore_ids` / `mark_switch_restored` on coordinator)
- **HACS/manifest:** renamed **Adaptive Cover (NET Fork)**; docs/issue tracker point at this repo
- **Tests:** `tests/test_coordinator_manual_persist.py` (13 tests)

### Prior NET Fork commits (already in main)

- **Window-open latch:** `_last_window_open_ts` survives flaky contact sensors (hold max open for `window_open_hold`)
- **Winter overrides anti-glare** in `normal_with_presence` climate path
- **Multi-window sensors:** `window_entity` accepts a list; separate `cloud_coverage` source
- **Config flow:** legacy single-string `window_entity` coerced to list for multi-select UI

---

## Tests

```bash
cd adaptive-cover
python3 -m pytest tests/ -v
```

Requires only `pytest` (HA libs stubbed in `tests/conftest.py`). **13 tests** cover Store load/save, malformed timestamp handling, and switch-restore gate.

---

## Features

- Individual configs for `vertical`, `horizontal`, and `tilted` covers
- **Basic** and **Climate** modes ([details below](#modes))
- Binary sensor: sun in front of window
- Start/end sun time sensors
- Auto manual-override detection (with NET Fork persistence across restart)
- Climate: weather, presence, lux/irradiance thresholds
- Adaptive control toggle, multi-cover, delta/time gates, sunset position

---

## Setup

Find window azimuth on [Open Street Map Compass](https://osmcompass.com/). Choose cover type in the integration config flow.

## Cover Types

|              | Vertical                      | Horizontal                      | Tilted                          |
| ------------ | ----------------------------- | ------------------------------- | ------------------------------- |
|              | ![alt text](images/image.png) | ![alt text](images/image-2.png) | ![alt text](images/image-1.png) |
| **Movement** | Up/Down                       | In/Out                          | Tilting                         |
|              | [variables](#vertical)        | [variables](#horizontal)        | [variables](#tilt)              |

## Modes

Two strategy modes: **basic** (sun position only) and **climate** (presence + temperature + weather).

```mermaid
  graph TD

  A[("fa:fa-sun Sundata")]
  A --> B["Basic Mode"]
  A --> C["Climate Mode"]

  subgraph "Basic Mode"
      B --> BA("Sun within field of view")

      BA --> |No| BC{{Default}}
      BC --> BE("Time between sunset and sunrise?")
      BE --> |Yes| BF["Return default"]
      BE --> |No| BG["Return Sunset default"]

      BA --> |Yes| BD("Elevation above 0?")
      BD --> |Yes| BH{{"Calculated Position"}}
      BD --> |No| BC
  end

  subgraph "Climate Mode"
      C --> CA("Check Presence")
  end

  subgraph "Occupants"
      CA --> |True| CB("Temperature above maximum comfort (summer)?")

      CB --> |Yes| CD("Transparent blind?")
      CB --> |No| CE("Lux/Irradiance below threshold or Weather is not sunny?")
  end
```

### Basic mode

Uses sun elevation/azimuth and field-of-view to compute shade position. Outside the sun window, uses default height or sunset position.

### Climate mode

Split into [presence](https://github.com/disruptivepatternmaterial/adaptive-cover#presence) and [no-presence](https://github.com/disruptivepatternmaterial/adaptive-cover#no-presence) strategies (see upstream docs in git history for full tables).

---

## Variables

### Common

| Name                 | Default | Range | Description                                      |
| -------------------- | ------- | ----- | ------------------------------------------------ |
| Azimuth              | None    | 0-360 | Window azimuth                                   |
| Default Height       | 50      | 0-100 | Position when sun not in FOV (day)               |
| Sunset Position      | 0       | 0-100 | Position after sunset                            |
| Field of View Left   | 90      | 0-180 | Degrees left of azimuth                          |
| Field of View Right  | 90      | 0-180 | Degrees right of azimuth                         |
| Minimum Elevation    | 0       | 0-90  | Ignore sun below this elevation                  |
| Maximum Elevation    | 90      | 0-90  | Ignore sun above this elevation                  |
| Manual Override Duration | 15 min | | How long manual hold lasts                    |
| Window Open Hold     | 30 min  |       | Keep covers open after window closes (flaky contacts) |

(Full variable tables for vertical/horizontal/tilt/climate/blindspot unchanged from upstream — see [basbruss/adaptive-cover](https://github.com/basbruss/adaptive-cover) for reference.)

---

## Entities

| Entity | Description |
|--------|-------------|
| `sensor.{type}_cover_position_{name}` | Calculated target position |
| `sensor.{type}_control_method_{name}` | `winter` / `summer` / `intermediate` (climate mode) |
| `binary_sensor.{type}_manual_override_{name}` | Any cover under manual hold |
| `switch.{type}_toggle_control_{name}` | Enable adaptive drives |
| `switch.{type}_manual_override_{name}` | Enable manual-override detection |
| `button.{type}_reset_manual_override_{name}` | Clear manual holds |

![entities](images/entities.png)

Climate mode adds `switch.{type}_climate_mode_{name}` and optional outside-temperature toggle.

---

## Credits

Original: [basbruss/adaptive-cover](https://github.com/basbruss/adaptive-cover).  
NET Fork: [disruptivepatternmaterial/adaptive-cover](https://github.com/disruptivepatternmaterial/adaptive-cover).
