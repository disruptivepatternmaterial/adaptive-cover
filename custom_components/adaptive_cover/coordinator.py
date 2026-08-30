"""The Coordinator for Adaptive Cover."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import datetime as dt
import logging
import time
from dataclasses import dataclass, field

import numpy as np
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import async_call_later, async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .config_context_adapter import ConfigContextAdapter

from .calculation import (
    AdaptiveHorizontalCover,
    AdaptiveTiltCover,
    AdaptiveVerticalCover,
    ClimateCoverData,
    ClimateCoverState,
    NormalCoverState,
)
from .const import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CONF_AWNING_ANGLE,
    CONF_AZIMUTH,
    CONF_BLIND_SPOT_ELEVATION,
    CONF_BLIND_SPOT_LEFT,
    CONF_BLIND_SPOT_RIGHT,
    CONF_CLIMATE_MODE,
    CONF_DEFAULT_HEIGHT,
    CONF_DELTA_POSITION,
    CONF_DELTA_TIME,
    CONF_DISTANCE,
    CONF_ENABLE_BLIND_SPOT,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_ENTITIES,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_HEIGHT_WIN,
    CONF_INTERP,
    CONF_INTERP_END,
    CONF_INTERP_LIST,
    CONF_INTERP_LIST_NEW,
    CONF_INTERP_START,
    CONF_INVERSE_STATE,
    CONF_IRRADIANCE_ENTITY,
    CONF_IRRADIANCE_THRESHOLD,
    CONF_LENGTH_AWNING,
    CONF_LUX_ENTITY,
    CONF_LUX_THRESHOLD,
    CONF_MANUAL_IGNORE_INTERMEDIATE,
    CONF_MANUAL_OVERRIDE_DURATION,
    CONF_MANUAL_OVERRIDE_RESET,
    CONF_MANUAL_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MAX_POSITION,
    CONF_MIN_ELEVATION,
    CONF_MIN_POSITION,
    CONF_OUTSIDE_THRESHOLD,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_RETURN_SUNSET,
    CONF_START_ENTITY,
    CONF_START_TIME,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_POS,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
    CONF_TILT_DEPTH,
    CONF_TILT_DISTANCE,
    CONF_TILT_MODE,
    CONF_TRANSPARENT_BLIND,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_STATE,
    CONF_WINDOW_ENTITY,
    CONF_WINDOW_OPEN_HOLD,
    CONF_CLOUD_COVERAGE_ENTITY,
    DEFAULT_WINDOW_OPEN_HOLD,
    DOMAIN,
    LOGGER,
    MIN_SETTLE_TOLERANCE,
)
from .helpers import get_datetime_from_str, get_last_updated, get_safe_state

_LOGGER = logging.getLogger(__name__)

UTC = dt.UTC


def settle_tolerance(manual_threshold: int | None) -> int:
    """Tolerance for deciding whether a cover reached its commanded target.

    Uses the configured manual_threshold but never less than
    MIN_SETTLE_TOLERANCE: a threshold of 0-4 would otherwise leave
    wait_for_target stuck on covers that settle a few percent off target.
    """
    return max(manual_threshold or 0, MIN_SETTLE_TOLERANCE)


def _normalize_manual_duration(raw_duration: dict | None) -> dict[str, int]:
    """Normalize manual override duration, including legacy sunset sentinel."""
    normalized = dict(raw_duration) if isinstance(raw_duration, dict) else {}
    minutes = normalized.get("minutes", 15)
    try:
        parsed_minutes = int(minutes)
    except (TypeError, ValueError):
        parsed_minutes = 15
    # Legacy compatibility: older builds stored "until sunset" as 9999.
    if parsed_minutes == 9999:
        parsed_minutes = 240
    normalized["minutes"] = parsed_minutes
    return normalized


def _now_matching(value: dt.datetime) -> dt.datetime:
    """Return 'now' with timezone-awareness matching a reference datetime."""
    if value.tzinfo is None:
        return dt.datetime.now()
    return dt.datetime.now(value.tzinfo)


@dataclass
class StateChangedData:
    """StateChangedData class."""

    entity_id: str
    old_state: State | None
    new_state: State | None


@dataclass
class AdaptiveCoverData:
    """AdaptiveCoverData class."""

    climate_mode_toggle: bool
    states: dict
    attributes: dict


class CoverCommandError(RuntimeError):
    """Raised when Home Assistant cannot dispatch a cover command."""


# hass.data[DOMAIN] maps entry_id -> coordinator and nothing else. Anything
# shared between entries goes one level down, under this reserved key, so a
# future `for coordinator in hass.data[DOMAIN].values()` cannot trip over it.
SHARED_DATA_KEY = "_shared"
_FORECAST_CACHE_KEY = "forecast_max_cache"


def shared_data(hass: HomeAssistant) -> dict:
    """Return the per-installation scratch space beside the entry coordinators."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(SHARED_DATA_KEY, {})


@dataclass
class _ForecastCache:
    """Per-weather-entity forecast high, shared across all config entries."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    fetched_at: dt.datetime | None = None
    value: float | None = None
    success: bool = False


class AdaptiveDataUpdateCoordinator(DataUpdateCoordinator[AdaptiveCoverData]):
    """Adaptive cover data update coordinator."""

    config_entry: ConfigEntry

    _WAIT_FOR_TARGET_TIMEOUT_S = 90
    _COVER_SERVICE_TIMEOUT_S = 10
    _FORECAST_CACHE_TTL = dt.timedelta(minutes=15)
    _FORECAST_FAILURE_RETRY_TTL = dt.timedelta(seconds=60)
    # A wedged weather integration must not hold the shared lock forever;
    # every other config entry pointing at the same entity queues behind it.
    _FORECAST_FETCH_TIMEOUT_S = 30

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:  # noqa: D107
        super().__init__(hass, LOGGER, name=DOMAIN, config_entry=config_entry)

        self.logger = ConfigContextAdapter(_LOGGER)
        self.logger.set_config_name(config_entry.data.get("name"))
        self._cover_type = config_entry.data.get("sensor_type")
        self._climate_mode = config_entry.options.get(CONF_CLIMATE_MODE, False)
        self._switch_mode = True if self._climate_mode else False
        self._inverse_state = config_entry.options.get(CONF_INVERSE_STATE, False)
        self._use_interpolation = config_entry.options.get(CONF_INTERP, False)
        self._track_end_time = config_entry.options.get(CONF_RETURN_SUNSET)
        self._temp_toggle = None
        self._control_toggle = None
        self._manual_toggle = None
        self._lux_toggle = None
        self._irradiance_toggle = None
        self._start_time = None
        self._sun_end_time = None
        self._sun_start_time = None
        self._sun_times_date = None
        self.manual_reset = config_entry.options.get(CONF_MANUAL_OVERRIDE_RESET, False)
        self.manual_duration = _normalize_manual_duration(
            config_entry.options.get(CONF_MANUAL_OVERRIDE_DURATION, {"minutes": 15})
        )
        self.manual_threshold = None
        self.state_change = False
        self.cover_state_change = False
        self.first_refresh = False
        self.timed_refresh = False
        self.climate_state = None
        self.control_method = "intermediate"
        self.state_change_data: StateChangedData | None = None
        self.manager = AdaptiveCoverManager(
            self.hass, config_entry.entry_id, self.manual_duration, self.logger
        )
        self.wait_for_target = {}
        self.target_call = {}
        self._wait_for_target_started_at: dict[str, float] = {}
        # Covers whose blocked-by-manual state has already been logged at
        # INFO this latch period (prevents once-per-refresh log spam).
        self._manual_skip_logged: set[str] = set()
        self.ignore_intermediate_states = config_entry.options.get(
            CONF_MANUAL_IGNORE_INTERMEDIATE, False
        )
        self._switches_restored: bool = False
        self.expected_restore_ids: set[str] = set()
        self.restored_ids: set[str] = set()
        self._update_listener = None
        self._scheduled_time = dt.datetime.now()

        self._cached_options = None

        self._max_forecast_temp: float | None = None

        # Window-open latch: monotonic timestamp of the most recent moment any
        # configured window/door binary_sensor reported "on". Used by
        # is_window_open to keep the cover at max for `window_open_hold`
        # seconds after the sensor flips back to "off", which protects
        # against flaky contact sensors that briefly report closed while the
        # door is physically open. Set to None until the sensor has been
        # seen "on" at least once.
        self._last_window_open_ts: float | None = None
        self.window_open_hold: int = DEFAULT_WINDOW_OPEN_HOLD
        self._window_latch_listeners: list = []
        self._window_known_states: dict[str, str] = {}
        self._command_timeout_listeners: dict[str, Callable[[], None]] = {}

    async def _async_update_forecast_max(self, weather_entity: str | None) -> None:
        """Fetch today's max temperature via the weather.get_forecasts service.

        HA removed the `forecast` state attribute in 2024.4; the only
        supported access path is the `weather.get_forecasts` service.
        Tries daily first, falls back to twice_daily (selecting today's
        daytime entry). On any failure or absence the cached value is
        cleared to None so predictive_heat does not silently use stale data.

        The cache lives in `hass.data`, not on the coordinator. Every config
        entry usually points at the same weather entity, so a per-coordinator
        cache multiplied the service call rate by the number of entries and
        all of them fired in the same event-loop tick. The lock makes those
        simultaneous refreshes coalesce into one call.
        """
        self._max_forecast_temp = None
        if not weather_entity:
            return
        if self.hass.states.get(weather_entity) is None:
            return

        cache = shared_data(self.hass).setdefault(_FORECAST_CACHE_KEY, {})
        entry = cache.get(weather_entity)
        if entry is None:
            entry = cache[weather_entity] = _ForecastCache()

        async with entry.lock:
            now_utc = dt.datetime.now(UTC)
            if entry.fetched_at is not None and (now_utc - entry.fetched_at) < (
                self._FORECAST_CACHE_TTL
                if entry.success
                else self._FORECAST_FAILURE_RETRY_TTL
            ):
                self._max_forecast_temp = entry.value
                return
            # Do not pre-clear entry.value. _async_fetch_forecast_max only
            # writes it on success, so blanking it here meant one failed fetch
            # served None to every config entry sharing this weather entity
            # for the whole failure TTL, disabling predictive_heat house-wide
            # over a transient error. Keep the last known good value and let
            # the TTL decide when it is too old to use.
            #
            # success is reset, though: it only selects which TTL applies, and
            # a stale True would grant a failed attempt the full 15 minutes.
            # _async_fetch_forecast_max sets it back to True once it has a
            # value.
            entry.success = False
            try:
                async with asyncio.timeout(self._FORECAST_FETCH_TIMEOUT_S):
                    await self._async_fetch_forecast_max(weather_entity, entry)
            except asyncio.CancelledError:
                # A reload or shutdown cancels us mid-fetch. Leaving fetched_at
                # unset matters: stamped, this half-finished entry would be
                # served as an authoritative "no forecast" to every config
                # entry sharing the weather entity until the retry TTL expired.
                entry.fetched_at = None
                raise
            except TimeoutError:
                # Caught here on purpose. asyncio.timeout raises TimeoutError,
                # which subclasses OSError and so Exception, and
                # _async_update_forecast_max is awaited unguarded inside
                # _async_refresh_data -- so letting it escape would be turned
                # into UpdateFailed and take every entity on the entry
                # unavailable because an *optional* forecast was slow.
                self.logger.warning(
                    "Forecast fetch for %s exceeded %s s; keeping the previous "
                    "value and retrying after %s",
                    weather_entity,
                    self._FORECAST_FETCH_TIMEOUT_S,
                    self._FORECAST_FAILURE_RETRY_TTL,
                )
            # Stamped after the call returns, so the TTL measures from when the
            # answer was known rather than from when we started asking.
            entry.fetched_at = dt.datetime.now(UTC)
        self._max_forecast_temp = entry.value

    async def _async_fetch_forecast_max(
        self, weather_entity: str, entry: _ForecastCache
    ) -> None:
        """Call weather.get_forecasts and store today's high on `entry`."""

        for forecast_type in ("daily", "twice_daily"):
            try:
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"type": forecast_type},
                    target={"entity_id": weather_entity},
                    blocking=True,
                    return_response=True,
                )
            except Exception as err:  # noqa: BLE001
                self.logger.debug(
                    "weather.get_forecasts(%s) for %s failed: %s",
                    forecast_type,
                    weather_entity,
                    err,
                )
                continue

            entity_data = (response or {}).get(weather_entity) or {}
            forecasts = entity_data.get("forecast") or []
            if not forecasts:
                continue

            today_entry = forecasts[0]
            if forecast_type == "twice_daily":
                # Home Assistant's date, not the OS process's: the forecast
                # datetimes are local wall-clock, so a UTC container would
                # match tomorrow's daytime entry all evening.
                today_iso = dt_util.now().date().isoformat()
                day_entries = [
                    f
                    for f in forecasts
                    if str(f.get("datetime", "")).startswith(today_iso)
                    and f.get("is_daytime", True)
                ]
                if day_entries:
                    today_entry = day_entries[0]

            temp_high = today_entry.get("temperature")
            if temp_high is not None:
                try:
                    entry.value = float(temp_high)
                except (TypeError, ValueError):
                    entry.value = None
                    continue
                self.logger.debug(
                    "Forecast high from %s (%s): %s",
                    weather_entity,
                    forecast_type,
                    entry.value,
                )
                entry.success = True
                return

    async def async_config_entry_first_refresh(self) -> None:
        """Config entry first refresh."""
        await self.manager.async_load()
        self.first_refresh = True
        await super().async_config_entry_first_refresh()
        self.logger.debug("Config entry first refresh")

    def set_expected_switch_ids(self, ids: set[str]) -> None:
        """Register the set of switch unique IDs that must restore before first drive."""
        self.expected_restore_ids = ids
        if not ids:
            self._switches_restored = True
            self.logger.debug(
                "No switches to restore; marking startup gate as restored"
            )
            self.hass.async_create_task(self.async_refresh())

    def mark_switch_restored(self, unique_id: str) -> None:
        """Record that a switch has restored its state."""
        self.restored_ids.add(unique_id)
        if self.expected_restore_ids and self.restored_ids >= self.expected_restore_ids:
            self._switches_restored = True
            self.logger.debug("All switches restored; scheduling first-drive refresh")
            self.hass.async_create_task(self.async_refresh())

    @callback
    def async_cancel_scheduled_callbacks(self) -> None:
        """Cancel all scheduled point-in-time callbacks for this coordinator."""
        self._async_cancel_update_listener()
        self._cancel_window_latch_listeners()
        for cancel in self._command_timeout_listeners.values():
            cancel()
        self._command_timeout_listeners.clear()

    def _cancel_window_latch_listeners(self) -> None:
        """Cancel pending window-close hold refreshes."""
        for cancel in self._window_latch_listeners:
            cancel()
        self._window_latch_listeners.clear()

    def _schedule_window_latch_release(
        self, entity_id: str, delay_seconds: float
    ) -> None:
        """Schedule the refresh that releases a confirmed-close hold."""
        self._cancel_window_latch_listeners()
        release_at = dt.datetime.now(UTC) + dt.timedelta(seconds=delay_seconds + 1)
        self.logger.debug(
            "Window %s became closed; scheduling latch release refresh at %s",
            entity_id,
            release_at,
        )
        cancel_listener = async_track_point_in_time(
            self.hass, self._async_release_window_latch, release_at
        )
        self._window_latch_listeners.append(cancel_listener)

    async def async_timed_refresh(self, event) -> None:
        """Control state at end time."""

        now = dt.datetime.now()
        # Initialize to None; assign only when a valid source exists so we
        # never reference an unbound variable below.
        time = None
        if self.end_time is not None:
            time = self.end_time
        if self.end_time_entity is not None:
            # Only override the config value when the entity is actually available;
            # an unavailable entity returns None and should not discard a valid
            # static end_time that was already assigned above.
            entity_time = get_safe_state(self.hass, self.end_time_entity)
            if entity_time is not None:
                time = entity_time

        self.logger.debug("Checking timed refresh. End time: %s, now: %s", time, now)

        if time is None:
            self.logger.debug("Timed refresh: end time is None, skipping")
            return

        parsed_time = get_datetime_from_str(time)
        if parsed_time is None:
            self.logger.debug(
                "Timed refresh: could not parse end time %r, skipping", time
            )
            return
        # Apply the same midnight rollover that _end_time uses: a configured
        # time of 00:00 means "end of day" so advance by one day so it isn't
        # immediately in the past when evaluated at midnight.
        if parsed_time.time() == dt.time(0, 0):
            parsed_time = parsed_time + dt.timedelta(days=1)
        # Use abs() so the callback still fires even when the event loop is
        # delayed past the 1-second gate, rather than silently skipping.
        time_check = abs(now - parsed_time)
        if time_check <= dt.timedelta(seconds=60):
            self.timed_refresh = True
            self.logger.debug("Timed refresh triggered (delta %s)", time_check)
            await self.async_refresh()
        else:
            self.logger.debug(
                "Timed refresh fired but delta %s > 60s threshold; skipping", time_check
            )

    async def async_check_entity_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Fetch and process state change event."""
        self.logger.debug("Entity state change")
        data = event.data
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        old_state = data.get("old_state")
        window_entities = getattr(self, "window_entities", []) or []
        is_window_event = entity_id in window_entities
        new_window_state = new_state.state if new_state is not None else None
        old_window_state = old_state.state if old_state is not None else None
        known_states = getattr(self, "_window_known_states", None)
        if known_states is None:
            known_states = self._window_known_states = {}
        previous_known_state = known_states.get(entity_id)
        if is_window_event and new_window_state in ("on", "off"):
            known_states[entity_id] = new_window_state

        if is_window_event and new_window_state != "off":
            # A safety contact is open or cannot prove that it is closed;
            # any pending release is obsolete.
            self._cancel_window_latch_listeners()
        elif (
            is_window_event
            and self.window_open_hold > 0
            and new_window_state == "off"
            and (
                previous_known_state == "on"
                or (previous_known_state is None and old_window_state == "on")
            )
        ):
            # The last definite contact state was open and it is now
            # explicitly closed. Hold for the full configured period.
            self._last_window_open_ts = time.monotonic()
            self._schedule_window_latch_release(entity_id, self.window_open_hold)
        elif is_window_event and new_window_state == "off":
            # Recovery from unknown/unavailable after a last-known closed
            # state is not a physical close and must not create a motor cycle.
            all_closed = all(
                (state := self.hass.states.get(window_id)) is not None
                and state.state == "off"
                for window_id in window_entities
            )
            if all_closed:
                elapsed = (
                    None
                    if self._last_window_open_ts is None
                    else time.monotonic() - self._last_window_open_ts
                )
                remaining = 0 if elapsed is None else self.window_open_hold - elapsed
                if remaining > 0:
                    self._schedule_window_latch_release(entity_id, remaining)
                else:
                    self._last_window_open_ts = None
                    self._cancel_window_latch_listeners()
        self.state_change = True
        await self.async_refresh()

    async def _async_release_window_latch(self, _now) -> None:
        """One-shot refresh fired after window_open_hold elapses.

        is_window_open re-evaluates on every refresh, so this just
        prompts the coordinator to recompute and let the cover drop
        back to its calculated position once the hold has expired.
        """
        self.logger.debug("Window-open latch release timer fired")
        self._window_latch_listeners.clear()
        self.state_change = True
        await self.async_refresh()

    async def async_check_cover_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Fetch and process state change event."""
        self.logger.debug("Cover state change")
        data = event.data
        if data["old_state"] is None:
            self.logger.debug("Old state is None")
            return
        if data["new_state"] is None:
            # Cover entity was removed from HA while the integration is running.
            self.logger.debug("New state is None (entity removed?), skipping")
            return
        self.state_change_data = StateChangedData(
            data["entity_id"], data["old_state"], data["new_state"]
        )
        # Transitions involving unavailable/unknown are availability changes,
        # not movement. A cover reconnecting (ESPHome/ZHA blip, HA restart)
        # re-reports its position; feeding that into manual-detect marks the
        # cover "manually controlled" whenever its position differs from the
        # calculated state, freezing adaptive control for the full reset
        # duration. Verified in production 2026-07-07: library shade latched
        # at the exact second of an unavailable->open(100) transition.
        old = self.state_change_data.old_state.state
        new = self.state_change_data.new_state.state
        if old in ("unknown", "unavailable") or new in ("unknown", "unavailable"):
            self.logger.debug(
                "Availability transition for %s (%s -> %s); skipping manual-detect",
                data["entity_id"],
                old,
                new,
            )
            return
        self.cover_state_change = True
        if not self._switches_restored:
            # docs/specs/behavioral-contract.md: "Cover events before switch
            # restore do not consume command-tracking." The drive paths are
            # gated on _switches_restored (see async_handle_first_refresh and
            # async_handle_cover_state_change) but this entry point was not, so
            # a cover reporting in during startup could clear the tracking for
            # a command the restored switches have not decided about yet.
            # Still refresh: the position is worth observing, just not
            # interpreting.
            self.logger.debug(
                "Cover event for %s before switch restore; observing position "
                "without consuming command-tracking",
                data["entity_id"],
            )
            await self.async_refresh()
            return
        self.process_entity_state_change()
        await self.async_refresh()

    def process_entity_state_change(self):
        """Process state change event."""
        event = self.state_change_data
        if event is None or event.new_state is None:
            return
        self.logger.debug("Processing state change event: %s", event)
        entity_id = event.entity_id
        if self.ignore_intermediate_states and event.new_state.state in [
            "opening",
            "closing",
        ]:
            self.logger.debug("Ignoring intermediate state change for %s", entity_id)
            return
        if self.wait_for_target.get(entity_id):
            position = event.new_state.attributes.get(
                "current_position"
                if self._cover_type != "cover_tilt"
                else "current_tilt_position"
            )
            target = self.target_call.get(entity_id)
            tolerance = settle_tolerance(self.manual_threshold)
            if target is None:
                self._clear_wait_for_target(entity_id, clear_target=True)
            elif position is not None and abs(position - target) <= tolerance:
                self._clear_wait_for_target(entity_id, clear_target=False)
                self.logger.debug(
                    "Position %s reached for %s (target %s, tolerance %s)",
                    position,
                    entity_id,
                    target,
                    tolerance,
                )
            else:
                started_at = self._wait_for_target_started_at.get(entity_id)
                if (
                    started_at is not None
                    and (time.monotonic() - started_at)
                    > self._WAIT_FOR_TARGET_TIMEOUT_S
                ):
                    self.logger.warning(
                        "Timed out waiting for %s to reach target %s; clearing wait state",
                        entity_id,
                        target,
                    )
                    # Keep target_call: slow covers (e.g. group entities
                    # aggregating several shades) legitimately take longer
                    # than the timeout. Dropping the target here would strip
                    # the commanded-target exemption mid-travel and the
                    # eventual settle report would be misclassified as a
                    # manual move. The manager pops the target when the cover
                    # settles within tolerance; the next drive overwrites it.
                    self._clear_wait_for_target(entity_id, clear_target=False)
            self.logger.debug("Wait for target: %s", self.wait_for_target)
        else:
            self.logger.debug("No wait for target call for %s", entity_id)

    @callback
    def _async_cancel_update_listener(self) -> None:
        """Cancel the scheduled update."""
        if self._update_listener:
            self._update_listener()
            self._update_listener = None

    def _clear_wait_for_target(
        self, entity_id: str, *, clear_target: bool = True
    ) -> None:
        """Clear command-tracking state for a single cover."""
        self._cancel_command_timeout(entity_id)
        self.wait_for_target[entity_id] = False
        if clear_target:
            self.target_call.pop(entity_id, None)
        self._wait_for_target_started_at.pop(entity_id, None)

    def _cancel_command_timeout(self, entity_id: str) -> None:
        """Cancel a pending command-tracking timeout for one cover."""
        listeners = getattr(self, "_command_timeout_listeners", None)
        if not listeners:
            return
        cancel = listeners.pop(entity_id, None)
        if cancel is not None:
            cancel()

    def _schedule_command_timeout(self, entity_id: str, target: int) -> None:
        """Release wait tracking even if the cover emits no later event."""
        listeners = getattr(self, "_command_timeout_listeners", None)
        if listeners is None:
            listeners = self._command_timeout_listeners = {}
        self._cancel_command_timeout(entity_id)

        @callback
        def _async_timeout(_now) -> None:
            listeners.pop(entity_id, None)
            if (
                self.wait_for_target.get(entity_id)
                and self.target_call.get(entity_id) == target
            ):
                self.logger.warning(
                    "Timed out waiting for %s to report target %s; releasing "
                    "wait state while retaining the commanded-target exemption",
                    entity_id,
                    target,
                )
                self._clear_wait_for_target(entity_id, clear_target=False)

        listeners[entity_id] = async_call_later(
            self.hass, self._WAIT_FOR_TARGET_TIMEOUT_S, _async_timeout
        )

    async def async_timed_end_time(self) -> None:
        """Control state at end time."""
        self.logger.debug("Scheduling end time update at %s", self._end_time)
        self._async_cancel_update_listener()
        self.logger.debug(
            "End time: %s, Track end time: %s, Scheduled time: %s, Condition: %s",
            self._end_time,
            self._track_end_time,
            self._scheduled_time,
            self._end_time > self._scheduled_time,
        )
        self._update_listener = async_track_point_in_time(
            self.hass, self.async_timed_refresh, self._end_time
        )
        self._scheduled_time = self._end_time

    async def _async_update_data(self) -> AdaptiveCoverData:
        """Fetch coordinator data with robust error handling."""
        try:
            return await self._async_refresh_data()
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Adaptive Cover update failed: {err}") from err

    async def _async_solar_times(self, normal_cover):
        """Return today's solar window, recomputing at most once per local day.

        The window comes from a full day of 5-minute samples, so it is built in
        the executor and cached. The cache is keyed on the date it was built
        for, in Home Assistant's timezone, because the two obvious shortcuts
        both misfire: keying on the result means a window the sun never reaches
        (solar_times() returns None, None) rebuilds all 289 samples every
        cycle, and comparing against a UTC date rolls the day over at the wrong
        hour for every user outside UTC.
        """
        today = dt_util.now().date()
        if self.first_refresh or self._sun_times_date != today:
            self.logger.debug("Calculating solar times")
            start, end = await self.hass.async_add_executor_job(
                normal_cover.solar_times
            )
            self._sun_start_time = start
            self._sun_end_time = end
            self._sun_times_date = today
            self.logger.debug("Sun start time: %s, Sun end time: %s", start, end)
        return self._sun_start_time, self._sun_end_time

    async def _async_refresh_data(self) -> AdaptiveCoverData:
        """Fetch coordinator data for this update cycle."""
        self.logger.debug("Updating data")
        if self.first_refresh:
            self._cached_options = self.config_entry.options

        options = self.config_entry.options
        self._update_options(options)

        # Get data for the blind
        cover_data = self.get_blind_data(options=options)

        # Update manager with covers
        self._update_manager_and_covers()

        # Access climate data if climate mode is enabled
        if self._climate_mode:
            await self._async_update_forecast_max(options.get(CONF_WEATHER_ENTITY))
            self.climate_mode_data(options, cover_data)
        else:
            self.logger.debug("Control method is %s", self.control_method)

        # calculate the state of the cover
        self.normal_cover_state = NormalCoverState(cover_data)
        self.logger.debug(
            "Determined normal cover state to be %s", self.normal_cover_state
        )

        self.default_state = int(round(self.normal_cover_state.get_state()))
        self.logger.debug("Determined default state to be %s", self.default_state)
        calculated_state = self.state
        window_open = self.is_window_open
        state = self.get_effective_state(
            calculated_state, options, window_open=window_open
        )

        await self.manager.reset_if_needed()

        end_time = self._end_time
        if end_time and self._track_end_time and end_time > self._scheduled_time:
            await self.async_timed_end_time()

        # Handle types of changes
        if self.state_change:
            await self.async_handle_state_change(
                state, options, window_open=window_open
            )
        if self.cover_state_change:
            await self.async_handle_cover_state_change(
                state, options, window_open=window_open
            )
        if self.first_refresh:
            await self.async_handle_first_refresh(
                state, options, window_open=window_open
            )
        if self.timed_refresh:
            await self.async_handle_timed_refresh(options, window_open=window_open)

        normal_cover = self.normal_cover_state.cover
        start, end = await self._async_solar_times(normal_cover)
        # Re-read the safety contact after every awaited handler. A door can
        # open mid-cycle; publishing the stale calculated value for even one
        # coordinator update lets an echo automation command that unsafe
        # target before the queued refresh repairs it.
        window_open = self.is_window_open
        state = self.get_effective_state(
            calculated_state, options, window_open=window_open
        )
        return AdaptiveCoverData(
            climate_mode_toggle=self.switch_mode,
            states={
                "state": state,
                "start": start,
                "end": end,
                "control": self.control_method,
                "sun_motion": bool(normal_cover.valid),
                "manual_override": self.manager.binary_cover_manual,
                "manual_list": self.manager.manual_controlled,
                "explanation": "window_open" if window_open else "auto",
            },
            attributes={
                "default": options.get(CONF_DEFAULT_HEIGHT),
                "sunset_default": options.get(CONF_SUNSET_POS),
                "sunset_offset": options.get(CONF_SUNSET_OFFSET),
                "azimuth_window": options.get(CONF_AZIMUTH),
                "field_of_view": [
                    options.get(CONF_FOV_LEFT),
                    options.get(CONF_FOV_RIGHT),
                ],
                "blind_spot": options.get(CONF_BLIND_SPOT_ELEVATION),
            },
        )

    async def async_handle_state_change(
        self, state: int, options, *, window_open: bool = False
    ):
        """Handle state change from tracked entities."""
        if window_open or self.is_window_open:
            await self._async_enforce_window_interlock(options)
        elif not self._switches_restored:
            self.logger.debug("State change deferred: switches not yet restored")
        elif self.control_toggle:
            for cover in self.entities:
                try:
                    await self.async_handle_call_service(cover, state, options)
                except CoverCommandError:
                    self.logger.error(
                        "Adaptive drive failed for %s; continuing with remaining covers",
                        cover,
                        exc_info=True,
                    )
        else:
            self.logger.debug("State change but control toggle is off")
        self.state_change = False
        self.logger.debug("State change handled")

    async def async_handle_cover_state_change(
        self,
        state: int,
        options,
        *,
        window_open: bool = False,
    ):
        """Handle state change from assigned covers."""
        if window_open or self.is_window_open:
            # A cover moving away from the safe target while a door/window is
            # open is a contested interlock, not a user preference. Never
            # retire the safety rule by latching manual override; immediately
            # reassert the safe target instead.
            await self._async_enforce_window_interlock(options)
        elif not self._switches_restored:
            # Switches have not restored yet; do not consume command-tracking.
            pass
        elif self._manual_toggle is None:
            pass
        elif self.manual_toggle and self.control_toggle:
            self.manager.handle_state_change(
                self.state_change_data,
                state,
                self._cover_type,
                self.manual_reset,
                self.wait_for_target,
                self.manual_threshold,
                target_call=self.target_call,
            )
            if self.state_change_data is not None:
                entity_id = self.state_change_data.entity_id
                if not self.wait_for_target.get(entity_id):
                    self._wait_for_target_started_at.pop(entity_id, None)
        elif self.state_change_data is not None:
            # Manual detection is disabled; drop wait AND target together.
            # Popping only target_call left wait_for_target stuck True with
            # target None, which suppressed detection after the switch was
            # turned back on (production 2026-08-12).
            self._clear_wait_for_target(
                self.state_change_data.entity_id, clear_target=True
            )
        self.cover_state_change = False
        self.logger.debug("Cover state change handled")

    async def async_handle_first_refresh(
        self, state: int, options, *, window_open: bool = False
    ):
        """Handle first refresh."""
        if window_open or self.is_window_open:
            await self._async_enforce_window_interlock(options)
            # Keep first_refresh armed until switch restoration completes so
            # the normal startup drive is not lost if the door closes first.
            if self._switches_restored:
                self.first_refresh = False
            return
        if not self._switches_restored:
            self.logger.debug("First refresh deferred: switches not yet restored")
            return
        if self.control_toggle:
            for cover in self.entities:
                if self.check_adaptive_time and self.check_position_delta(
                    cover, state, options
                ):
                    if self.manager.is_cover_manual(cover):
                        self._log_manual_skip(cover, state)
                        continue
                    try:
                        await self.async_set_position(cover, state)
                    except CoverCommandError:
                        self.logger.error(
                            "Startup drive failed for %s; continuing with "
                            "remaining covers",
                            cover,
                            exc_info=True,
                        )
        else:
            self.logger.debug("First refresh but control toggle is off")
        self.first_refresh = False
        self.logger.debug("First refresh handled")

    async def async_handle_timed_refresh(self, options, *, window_open: bool = False):
        """Handle timed refresh."""
        if window_open or self.is_window_open:
            await self._async_enforce_window_interlock(options)
            self.timed_refresh = False
            return
        self.logger.debug(
            "This is a timed refresh, using sunset position: %s",
            options.get(CONF_SUNSET_POS),
        )
        if self.control_toggle:
            for cover in self.entities:
                try:
                    await self.async_set_manual_position(
                        cover,
                        (
                            inverse_state(options.get(CONF_SUNSET_POS))
                            if self._inverse_state
                            else options.get(CONF_SUNSET_POS)
                        ),
                    )
                except CoverCommandError:
                    self.logger.error(
                        "Timed drive failed for %s; continuing with remaining covers",
                        cover,
                        exc_info=True,
                    )
        else:
            self.logger.debug("Timed refresh but control toggle is off")
        self.timed_refresh = False
        self.logger.debug("Timed refresh handled")

    async def async_handle_call_service(self, entity, state: int, options):
        """Handle call service."""
        if self.is_window_open:
            target = self._window_open_target(options)
            await self.async_set_manual_position(entity, target)
            return
        if (
            self.check_adaptive_time
            and self.check_position_delta(entity, state, options)
            and self.check_time_delta(entity)
        ):
            if self.manager.is_cover_manual(entity):
                self._log_manual_skip(entity, state)
                return
            self._manual_skip_logged.discard(entity)
            await self.async_set_position(entity, state)

    def _log_manual_skip(self, entity: str, state: int) -> None:
        """Log at INFO, once per latch, that a drive was blocked by manual override."""
        if entity in self._manual_skip_logged:
            return
        self._manual_skip_logged.add(entity)
        self.logger.info(
            "Adaptive drive to %s skipped for %s: manual override is active "
            "(clears per configured duration or via the reset button)",
            state,
            entity,
        )

    @property
    def is_window_open(self) -> bool:
        """Return True if any configured window/door is treated as open.

        A safety contact is closed only when Home Assistant explicitly reports
        "off". Missing, unknown, and unavailable contacts fail safe as open.
        After every transition to an explicit "off", the hold keeps the
        interlock active for `window_open_hold` seconds to absorb flaky contact
        reports. Set `window_open_hold` to 0 to disable only that close hold.
        """
        window_entities = getattr(self, "window_entities", []) or []
        if not window_entities:
            return False
        for entity_id in window_entities:
            state = self.hass.states.get(entity_id)
            if state is None or state.state != "off":
                return True
        now = time.monotonic()
        if (
            self._last_window_open_ts is not None
            and self.window_open_hold > 0
            and (now - self._last_window_open_ts) < self.window_open_hold
        ):
            return True
        return False

    def get_effective_state(
        self,
        calculated_state: int,
        options,
        *,
        window_open: bool | None = None,
    ) -> int:
        """Return the single target exposed to callers and managed covers."""
        if window_open is None:
            window_open = self.is_window_open
        if window_open:
            return self._window_open_target(options)
        return int(round(calculated_state))

    def _window_open_target(self, options) -> int:
        """Position to drive covers to when window is open.

        Uses a configured maximum as the hardware-safe opening limit;
        otherwise drives fully open (100). Honors inverse_state.
        """
        max_pos = options.get(CONF_MAX_POSITION)
        target = max_pos if max_pos is not None else 100
        if self._inverse_state:
            target = inverse_state(target)
        return int(round(target))

    async def _async_enforce_window_interlock(self, options) -> None:
        """Enforce safety without making coordinator data unavailable."""
        try:
            await self._async_drive_to_max_open(options)
        except CoverCommandError:
            self.logger.error(
                "Window/door interlock could not drive every cover; the "
                "calculated safety state remains available",
                exc_info=True,
            )

    async def _async_drive_to_max_open(self, options) -> None:
        """Drive every cover in this entry to the window-open target."""
        target = self._window_open_target(options)
        self.logger.debug(
            "Window open on one of %s; driving covers to %s",
            getattr(self, "window_entities", []),
            target,
        )
        first_error: CoverCommandError | None = None
        for cover in self.entities:
            try:
                await self.async_set_manual_position(cover, target)
            except CoverCommandError as err:
                # One unavailable motor must not prevent later covers in the
                # same doorway from receiving the safety command. Preserve
                # observability by re-raising after every cover was attempted.
                if first_error is None:
                    first_error = err
        if first_error is not None:
            raise first_error

    async def async_set_position(self, entity, state: int):
        """Call service to set cover position."""
        await self.async_set_manual_position(entity, state)

    async def async_set_manual_position(self, entity, state):
        """Call service to set cover position."""
        # Re-resolve the safety target at the final dispatch boundary. A door
        # can open after an update cycle snapshots its inputs but before a
        # button, switch, or normal adaptive path reaches this method; no
        # caller is allowed to send that stale closing target.
        if self.is_window_open:
            state = self._window_open_target(self.config_entry.options)
        if self.wait_for_target.get(entity) and self.target_call.get(entity) == state:
            self.logger.debug(
                "Target %s is already in flight for %s; suppressing duplicate command",
                state,
                entity,
            )
            return
        if self.check_position(entity, state):
            service = SERVICE_SET_COVER_POSITION
            service_data = {}
            service_data[ATTR_ENTITY_ID] = entity

            if self._cover_type == "cover_tilt":
                service = SERVICE_SET_COVER_TILT_POSITION
                service_data[ATTR_TILT_POSITION] = state
            else:
                service_data[ATTR_POSITION] = state

            self.wait_for_target[entity] = True
            self.target_call[entity] = state
            self._wait_for_target_started_at[entity] = time.monotonic()
            self.logger.debug(
                "Set wait for target %s and target call %s",
                self.wait_for_target,
                self.target_call,
            )
            self.logger.debug("Run %s with data %s", service, service_data)
            try:
                async with asyncio.timeout(self._COVER_SERVICE_TIMEOUT_S):
                    await self.hass.services.async_call(
                        COVER_DOMAIN, service, service_data, blocking=True
                    )
                if self.wait_for_target.get(entity):
                    self._schedule_command_timeout(entity, state)
            except Exception as err:
                # Roll back rather than reorder. The tracking has to be in
                # place before the await, because HA can deliver the resulting
                # state-change event while we are suspended here. But if the
                # call raises -- an unavailable cover, a failing cover
                # integration -- nothing else clears it, and for the next
                # _WAIT_FOR_TARGET_TIMEOUT_S seconds a genuine manual move is
                # attributed to us and does not latch manual override.
                self._clear_wait_for_target(entity, clear_target=True)
                self.logger.warning(
                    "Failed to drive %s to %s; cleared command tracking so a "
                    "manual move is still detected",
                    entity,
                    state,
                    exc_info=True,
                )
                raise CoverCommandError(f"Failed to drive {entity} to {state}") from err

    def _update_options(self, options):
        """Update options."""
        self.entities = options.get(CONF_ENTITIES, [])
        # window_entity may be a single string (legacy single-select) or a list
        # (modern multi-select). Always normalize to a list so the rest of the
        # coordinator can treat them uniformly.
        raw_window = options.get(CONF_WINDOW_ENTITY) or []
        if isinstance(raw_window, str):
            self.window_entities = [raw_window]
        else:
            self.window_entities = list(raw_window)
        previous_known = getattr(self, "_window_known_states", {})
        self._window_known_states = {
            entity_id: previous_known[entity_id]
            for entity_id in self.window_entities
            if entity_id in previous_known
        }
        for entity_id in self.window_entities:
            state = self.hass.states.get(entity_id)
            if (
                entity_id not in self._window_known_states
                and state is not None
                and state.state in ("on", "off")
            ):
                self._window_known_states[entity_id] = state.state
        try:
            self.window_open_hold = int(
                options.get(CONF_WINDOW_OPEN_HOLD, DEFAULT_WINDOW_OPEN_HOLD)
            )
        except (TypeError, ValueError):
            self.window_open_hold = DEFAULT_WINDOW_OPEN_HOLD
        self.min_change = options.get(CONF_DELTA_POSITION, 1)
        self.time_threshold = options.get(CONF_DELTA_TIME, 2)
        self.start_time = options.get(CONF_START_TIME)
        self.start_time_entity = options.get(CONF_START_ENTITY)
        self.end_time = options.get(CONF_END_TIME)
        self.end_time_entity = options.get(CONF_END_ENTITY)
        self.manual_reset = options.get(CONF_MANUAL_OVERRIDE_RESET, False)
        self.manual_duration = _normalize_manual_duration(
            options.get(CONF_MANUAL_OVERRIDE_DURATION, {"minutes": 15})
        )
        self.manager.set_reset_duration(self.manual_duration)
        self.manual_threshold = options.get(CONF_MANUAL_THRESHOLD)
        self.start_value = options.get(CONF_INTERP_START)
        self.end_value = options.get(CONF_INTERP_END)
        self.normal_list = options.get(CONF_INTERP_LIST)
        self.new_list = options.get(CONF_INTERP_LIST_NEW)

    def _update_manager_and_covers(self):
        self.manager.add_covers(self.entities)
        if self._switches_restored and self._manual_toggle is False:
            for entity in self.manager.manual_controlled:
                self.manager.reset(entity)

    def get_blind_data(self, options):
        """Assign correct class for type of blind."""
        if self._cover_type == "cover_blind":
            cover_data = AdaptiveVerticalCover(
                self.hass,
                self.logger,
                *self.pos_sun,
                *self.common_data(options),
                *self.vertical_data(options),
            )
        elif self._cover_type == "cover_awning":
            cover_data = AdaptiveHorizontalCover(
                self.hass,
                self.logger,
                *self.pos_sun,
                *self.common_data(options),
                *self.vertical_data(options),
                *self.horizontal_data(options),
            )
        elif self._cover_type == "cover_tilt":
            cover_data = AdaptiveTiltCover(
                self.hass,
                self.logger,
                *self.pos_sun,
                *self.common_data(options),
                *self.tilt_data(options),
            )
        else:
            raise ValueError(
                f"Unknown cover type '{self._cover_type}'. "
                "Expected one of: cover_blind, cover_awning, cover_tilt."
            )
        return cover_data

    @property
    def check_adaptive_time(self):
        """Check if time is within start and end times."""
        if self._start_time and self._end_time and self._start_time > self._end_time:
            self.logger.error("Start time is after end time")
        return self.before_end_time and self.after_start_time

    @property
    def after_start_time(self):
        """Check if time is after start time."""
        if self.start_time_entity is not None:
            raw = get_safe_state(self.hass, self.start_time_entity)
            if raw is None:
                # Entity unavailable — treat as "start time not yet reached"
                # so covers are not driven while the time source is offline.
                self.logger.debug(
                    "Start time entity %s unavailable; deferring adaptive time",
                    self.start_time_entity,
                )
                return False
            time = get_datetime_from_str(raw)
            if time is None:
                self.logger.debug(
                    "Start time entity %s returned unparseable value %r; deferring",
                    self.start_time_entity,
                    raw,
                )
                return False
            self._start_time = time
            now = _now_matching(time)
            self.logger.debug(
                "Start time: %s, now: %s, now >= time: %s ", time, now, now >= time
            )
            return now >= time
        if self.start_time is not None:
            time = get_datetime_from_str(self.start_time)
            self._start_time = time
            if time is None:
                return False
            now = _now_matching(time)
            self.logger.debug(
                "Start time: %s, now: %s, now >= time: %s", time, now, now >= time
            )
            return now >= time
        return True

    @property
    def _end_time(self) -> dt.datetime | None:
        """Get end time."""
        time = None
        if self.end_time_entity is not None:
            time = get_datetime_from_str(
                get_safe_state(self.hass, self.end_time_entity)
            )
        elif self.end_time is not None:
            time = get_datetime_from_str(self.end_time)
            if time is None:
                return None
            if time.time() == dt.time(0, 0):
                time = time + dt.timedelta(days=1)
        return time

    @property
    def before_end_time(self):
        """Check if time is before end time."""
        end_time = self._end_time
        if end_time is not None:
            now = _now_matching(end_time)
            self.logger.debug(
                "End time: %s, now: %s, now < time: %s",
                end_time,
                now,
                now < end_time,
            )
            return now < end_time
        return True

    def _get_current_position(self, entity) -> int | None:
        """Get current position of cover."""
        state = self.hass.states.get(entity)
        if self._cover_type == "cover_tilt":
            return state.attributes.get("current_tilt_position") if state else None
        return state.attributes.get("current_position") if state else None

    def check_position(self, entity, state):
        """Check if position is different as state."""
        position = self._get_current_position(entity)
        if position is not None:
            tolerance = settle_tolerance(self.manual_threshold)
            return abs(position - state) > tolerance
        self.logger.debug(
            "Cannot read position for %s (entity unavailable?); skipping move to %s",
            entity,
            state,
        )
        return False

    def check_position_delta(self, entity, state: int, options):
        """Check cover positions to reduce calls."""
        position = self._get_current_position(entity)
        if position is not None:
            condition = abs(position - state) >= self.min_change
            self.logger.debug(
                "Entity: %s,  position: %s, state: %s, delta position: %s, min_change: %s, condition: %s",
                entity,
                position,
                state,
                abs(position - state),
                self.min_change,
                condition,
            )
            if state in [
                options.get(CONF_SUNSET_POS),
                options.get(CONF_DEFAULT_HEIGHT),
                0,
                100,
            ]:
                condition = True
            return condition
        return True

    def check_time_delta(self, entity):
        """Check if time delta is passed."""
        now = dt.datetime.now(UTC)
        last_updated = get_last_updated(entity, self.hass)
        if last_updated is not None:
            condition = now - last_updated >= dt.timedelta(minutes=self.time_threshold)
            self.logger.debug(
                "Entity: %s, time delta: %s, threshold: %s, condition: %s",
                entity,
                now - last_updated,
                self.time_threshold,
                condition,
            )
            return condition
        return True

    @property
    def pos_sun(self):
        """Fetch information for sun position."""
        state = self.hass.states.get("sun.sun")
        if state is None:
            self.logger.debug(
                "sun.sun unavailable; using safe fallback azimuth/elevation for this cycle"
            )
            return [0.0, -90.0]
        azimuth = state.attributes.get("azimuth")
        elevation = state.attributes.get("elevation")
        if azimuth is None or elevation is None:
            self.logger.debug(
                "sun.sun missing azimuth/elevation; using safe fallback for this cycle"
            )
            return [0.0, -90.0]
        return [
            azimuth,
            elevation,
        ]

    def common_data(self, options):
        """Update shared parameters."""
        return [
            options.get(CONF_SUNSET_POS),
            options.get(CONF_SUNSET_OFFSET),
            options.get(CONF_SUNRISE_OFFSET, options.get(CONF_SUNSET_OFFSET)),
            self.hass.config.time_zone,
            options.get(CONF_FOV_LEFT),
            options.get(CONF_FOV_RIGHT),
            options.get(CONF_AZIMUTH),
            options.get(CONF_DEFAULT_HEIGHT),
            options.get(CONF_MAX_POSITION),
            options.get(CONF_MIN_POSITION),
            options.get(CONF_ENABLE_MAX_POSITION, False),
            options.get(CONF_ENABLE_MIN_POSITION, False),
            options.get(CONF_BLIND_SPOT_LEFT),
            options.get(CONF_BLIND_SPOT_RIGHT),
            options.get(CONF_BLIND_SPOT_ELEVATION),
            options.get(CONF_ENABLE_BLIND_SPOT, False),
            options.get(CONF_MIN_ELEVATION, None),
            options.get(CONF_MAX_ELEVATION, None),
        ]

    def get_climate_data(self, options):
        """Update climate data."""
        return [
            self.hass,
            self.logger,
            options.get(CONF_TEMP_ENTITY),
            options.get(CONF_TEMP_LOW),
            options.get(CONF_TEMP_HIGH),
            options.get(CONF_PRESENCE_ENTITY),
            options.get(CONF_WEATHER_ENTITY),
            options.get(CONF_WEATHER_STATE),
            options.get(CONF_OUTSIDETEMP_ENTITY),
            self._temp_toggle,
            self._cover_type,
            options.get(CONF_TRANSPARENT_BLIND),
            options.get(CONF_LUX_ENTITY),
            options.get(CONF_IRRADIANCE_ENTITY),
            options.get(CONF_LUX_THRESHOLD),
            options.get(CONF_IRRADIANCE_THRESHOLD),
            options.get(CONF_OUTSIDE_THRESHOLD),
            self._lux_toggle,
            self._irradiance_toggle,
            options.get(CONF_CLOUD_COVERAGE_ENTITY),
        ]

    def climate_mode_data(self, options, cover_data):
        """Update climate mode data and control method."""
        climate = ClimateCoverData(*self.get_climate_data(options))
        climate.max_forecast_temp = self._max_forecast_temp
        # Construct once, reuse for both state and climate_data to avoid
        # running the full decision tree twice per update cycle.
        climate_cover = ClimateCoverState(cover_data, climate)
        self.climate_state = int(round(climate_cover.get_state()))
        climate_data = climate_cover.climate_data
        # Reset to the default before applying season-specific overrides so
        # that transitions (e.g. summer → neither) don't leave a stale value.
        self.control_method = "intermediate"
        if self.switch_mode:
            # No overlap check. ClimateCoverData.is_winter returns False
            # whenever is_summer holds, and the two reads below have no await
            # between them, so `is_summer and is_winter` was unreachable --
            # a trip-wire that could not trip. The real cause of the overlap
            # this once tried to report was a stored outside-temperature
            # threshold of 0; see async_migrate_entry.
            is_summer = climate_data.is_summer
            is_winter = climate_data.is_winter
            if is_summer:
                self.control_method = "summer"
            elif is_winter:
                self.control_method = "winter"
        self.logger.debug(
            "Climate mode control method was set to %s", self.control_method
        )

    def vertical_data(self, options):
        """Update data for vertical blinds."""
        return [
            options.get(CONF_DISTANCE),
            options.get(CONF_HEIGHT_WIN),
        ]

    def horizontal_data(self, options):
        """Update data for horizontal blinds."""
        return [
            options.get(CONF_LENGTH_AWNING),
            options.get(CONF_AWNING_ANGLE),
        ]

    def tilt_data(self, options):
        """Update data for tilted blinds."""
        return [
            options.get(CONF_TILT_DISTANCE),
            options.get(CONF_TILT_DEPTH),
            options.get(CONF_TILT_MODE),
        ]

    @property
    def state(self) -> int:
        """Handle the output of the state based on mode."""
        self.logger.debug(
            "Basic position: %s; Climate position: %s; Using climate position? %s",
            self.default_state,
            self.climate_state,
            self._switch_mode,
        )
        if self._switch_mode:
            state = self.climate_state
        else:
            state = self.default_state

        if self._use_interpolation:
            self.logger.debug("Interpolating position: %s", state)
            state = self.interpolate_states(state)

        if self._inverse_state and self._use_interpolation:
            self.logger.info(
                "Inverse state is not supported with interpolation, you can inverse the state by arranging the list from high to low"
            )

        if self._inverse_state and not self._use_interpolation:
            state = inverse_state(state)
            self.logger.debug("Inversed position: %s", state)

        state = int(round(state))
        self.logger.debug("Final position to use: %s", state)
        return state

    def interpolate_states(self, state):
        """Interpolate states."""
        normal_range = [0, 100]
        new_range = []
        # Explicit None checks: an endpoint of 0 is a valid configured value
        # and must not be treated as "unset".
        if self.start_value is not None and self.end_value is not None:
            new_range = [self.start_value, self.end_value]
        if self.normal_list and self.new_list:
            normal_range = list(map(int, self.normal_list))
            new_range = list(map(int, self.new_list))
        if new_range:
            state = np.interp(state, normal_range, new_range)
            # At the range edges, command a true full close/open (upstream
            # behavior for covers whose usable band excludes 0/100). elif
            # prevents a double snap when new_range[-1] == 0.
            if state == new_range[0]:
                state = 0
            elif state == new_range[-1]:
                state = 100
        return state

    @property
    def switch_mode(self):
        """Let switch toggle climate mode."""
        return self._switch_mode

    @switch_mode.setter
    def switch_mode(self, value):
        self._switch_mode = value

    @property
    def temp_toggle(self):
        """Let switch toggle between inside or outside temperature."""
        return self._temp_toggle

    @temp_toggle.setter
    def temp_toggle(self, value):
        self._temp_toggle = value

    @property
    def control_toggle(self):
        """Toggle automation."""
        return self._control_toggle

    @control_toggle.setter
    def control_toggle(self, value):
        self._control_toggle = value

    @property
    def manual_toggle(self):
        """Toggle automation."""
        return self._manual_toggle

    @manual_toggle.setter
    def manual_toggle(self, value):
        self._manual_toggle = value

    @property
    def lux_toggle(self):
        """Toggle automation."""
        return self._lux_toggle

    @lux_toggle.setter
    def lux_toggle(self, value):
        self._lux_toggle = value

    @property
    def irradiance_toggle(self):
        """Toggle automation."""
        return self._irradiance_toggle

    @irradiance_toggle.setter
    def irradiance_toggle(self, value):
        self._irradiance_toggle = value


class AdaptiveCoverManager:
    """Track position changes."""

    STORE_VERSION = 1

    def __init__(
        self, hass: HomeAssistant, entry_id: str, reset_duration: dict[str, int], logger
    ) -> None:
        """Initialize the AdaptiveCoverManager."""
        self.covers: set[str] = set()
        self.manual_control: dict[str, bool] = {}
        self.manual_control_time: dict[str, dt.datetime] = {}
        self.logger = logger
        self.set_reset_duration(reset_duration)
        self._hass = hass
        self._store: Store = Store(
            hass,
            self.STORE_VERSION,
            f"adaptive_cover.{entry_id}.manual_state",
        )

    def set_reset_duration(self, reset_duration: dict[str, int] | None) -> None:
        """Update manual reset duration, normalizing legacy and invalid values."""
        normalized = _normalize_manual_duration(reset_duration)
        try:
            self.reset_duration = dt.timedelta(**normalized)
        except (TypeError, ValueError, OverflowError):
            self.logger.warning(
                "Invalid manual_override_duration %s; falling back to 15 minutes",
                reset_duration,
            )
            self.reset_duration = dt.timedelta(minutes=15)

    async def async_load(self) -> None:
        """Restore persisted manual state from storage."""
        data = await self._store.async_load()
        if not data:
            return
        self.manual_control = {
            k: bool(v) for k, v in data.get("manual_control", {}).items()
        }
        raw_times = data.get("manual_control_time", {})
        for entity_id, ts in raw_times.items():
            with suppress(ValueError, TypeError):
                self.manual_control_time[entity_id] = dt.datetime.fromisoformat(ts)
        self.logger.debug("Restored manual state: %s", list(self.manual_control.keys()))

    def _schedule_save(self) -> None:
        """Schedule an async save without blocking callers."""
        self._hass.async_create_task(self._async_save())

    async def _async_save(self) -> None:
        """Persist current manual state to storage."""
        await self._store.async_save(
            {
                "manual_control": dict(self.manual_control),
                "manual_control_time": {
                    k: v.isoformat() for k, v in self.manual_control_time.items()
                },
            }
        )

    def add_covers(self, entity):
        """Update set with entities."""
        self.covers.update(entity)

    def handle_state_change(
        self,
        states_data,
        our_state,
        blind_type,
        allow_reset,
        wait_target_call,
        manual_threshold,
        target_call=None,
    ):
        """Process state change event."""
        event = states_data
        if event is None:
            return
        entity_id = event.entity_id
        if entity_id not in self.covers:
            return
        new_state = event.new_state
        if new_state is None:
            return
        new_state_name = new_state.state
        old_state_name = None if event.old_state is None else event.old_state.state
        waiting = bool(wait_target_call.get(entity_id))
        target = None if target_call is None else target_call.get(entity_id)
        has_commanded_target = target is not None

        if blind_type == "cover_tilt":
            new_position = new_state.attributes.get("current_tilt_position")
        else:
            new_position = new_state.attributes.get("current_position")

        # Mid-travel of an integration-commanded move is not manual, even
        # after wait_for_target timed out (target_call still pending).
        if new_state_name in ("opening", "closing") and (
            waiting or has_commanded_target
        ):
            return
        # Position ticks that stay "open"/"closed" (no travel states) are
        # also mid-drive, not a user stop. A user stop is travel→settled
        # at a position that is not the commanded target.
        if (
            waiting
            and old_state_name in ("open", "closed")
            and new_state_name in ("open", "closed")
        ):
            return
        if waiting:
            wait_target_call[entity_id] = False

        # The integration just commanded this position; not a manual
        # change. Without this guard, integration-initiated drives to a
        # non-sun-tracked target (e.g. window-open at max, sunset_pos)
        # would be misclassified as manual control because `our_state`
        # is the sun-tracked value but `new_position` is whatever target
        # the coordinator actually issued via `async_set_manual_position`.
        # ZHA/Tuya covers commonly report `current_position` off the
        # commanded target by a few percent (e.g. commanded 100,
        # reported 99) so we allow a tolerance equal to the user-
        # configured `manual_threshold` (the same window used below for
        # human-vs-machine detection), with a 5% floor when not set.
        if has_commanded_target and new_position is not None:
            tolerance = settle_tolerance(manual_threshold)
            if abs(target - new_position) <= tolerance:
                self.logger.debug(
                    "Cover %s reached integration-commanded target %s "
                    "(reported %s, tolerance %s); retaining it as the last "
                    "commanded target and skipping manual-detect",
                    entity_id,
                    target,
                    new_position,
                    tolerance,
                )
                return
            # Off-target settle: drop the stale command so a later report
            # near the old target cannot be exempted as "commanded".
            target_call.pop(entity_id, None)

        if new_position is None:
            self.logger.debug(
                "Cover %s reported no position attribute (mid-travel or unavailable); "
                "skipping manual-detect",
                entity_id,
            )
            return

        if new_position != our_state:
            if (
                manual_threshold is not None
                and abs(our_state - new_position) < manual_threshold
            ):
                self.logger.debug(
                    "Position change is less than threshold %s for %s",
                    manual_threshold,
                    entity_id,
                )
                return
            # INFO on purpose: a manual latch pauses adaptive control for
            # hours and must be diagnosable from the HA log, not just the
            # binary_sensor. This fires at most once per external move.
            self.logger.info(
                "Manual override detected for %s: reported position %s deviates "
                "from calculated %s (threshold %s); adaptive control paused %s",
                entity_id,
                new_position,
                our_state,
                manual_threshold,
                (
                    "until manually reset"
                    if self.reset_duration <= dt.timedelta(0)
                    else f"for {self.reset_duration}"
                ),
            )
            self.logger.debug(
                "Set manual control for %s, reset_allowed: %s",
                entity_id,
                allow_reset,
            )
            self.mark_manual_control(entity_id)
            self.set_last_updated(entity_id, new_state, allow_reset)

    def set_last_updated(self, entity_id, new_state, allow_reset):
        """Set last updated time for manual control."""
        if entity_id not in self.manual_control_time or allow_reset:
            last_updated = new_state.last_updated
            self.manual_control_time[entity_id] = last_updated
            self.logger.debug(
                "Updating last updated for manual control to %s for %s. Allow reset:%s",
                last_updated,
                entity_id,
                allow_reset,
            )
            self._schedule_save()
        elif not allow_reset:
            self.logger.debug(
                "Already manual control time specified for %s, reset is not allowed by user setting:%s",
                entity_id,
                allow_reset,
            )

    def mark_manual_control(self, cover: str) -> None:
        """Mark cover as under manual control."""
        self.manual_control[cover] = True
        self._schedule_save()

    async def reset_if_needed(self):
        """Reset manual control state of the covers."""
        # A zero (or negative) duration is the "none" select option and means
        # "never auto-reset" — not "reset immediately". Manual reset stays
        # available via the reset button.
        if self.reset_duration <= dt.timedelta(0):
            return
        current_time = dt.datetime.now(UTC)
        manual_control_time_copy = dict(self.manual_control_time)
        for entity_id, last_updated in manual_control_time_copy.items():
            if current_time - last_updated > self.reset_duration:
                self.logger.debug(
                    "Resetting manual override for %s, because duration has elapsed",
                    entity_id,
                )
                self.reset(entity_id)

    def reset(self, entity_id):
        """Reset manual control for a cover."""
        was_manual = self.manual_control.get(entity_id, False)
        self.manual_control[entity_id] = False
        self.manual_control_time.pop(entity_id, None)
        if was_manual:
            self.logger.info(
                "Manual override cleared for %s; resuming adaptive control",
                entity_id,
            )
        self._schedule_save()

    def is_cover_manual(self, entity_id):
        """Check if a cover is under manual control."""
        return self.manual_control.get(entity_id, False)

    @property
    def binary_cover_manual(self):
        """Check if any cover is under manual control."""
        return any(value for value in self.manual_control.values())

    @property
    def manual_controlled(self):
        """Get the list of covers under manual control."""
        return [k for k, v in self.manual_control.items() if v]


def inverse_state(state: int) -> int:
    """Inverse state."""
    return 100 - state
