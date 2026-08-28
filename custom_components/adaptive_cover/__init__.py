"""The Adaptive Cover integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import (
    async_track_state_change_event,
)

from .const import (
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_END_ENTITY,
    CONF_ENTITIES,
    CONF_OUTSIDE_THRESHOLD,
    CONF_PRESENCE_ENTITY,
    CONF_START_ENTITY,
    CONF_TEMP_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_ENTITY,
    DOMAIN,
)
from .coordinator import (
    SHARED_DATA_KEY,
    AdaptiveDataUpdateCoordinator,
    _FORECAST_CACHE_KEY,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Drop an outside-temperature threshold that was never chosen by the user.

    CLIMATE_OPTIONS declared `vol.Optional(CONF_OUTSIDE_THRESHOLD, default=0)`.
    voluptuous materialises a default while validating rather than only
    offering it in the form, so every entry created under that schema stored a
    threshold of 0 whether or not the field was touched. Zero is not a usable
    threshold -- `outside_high` degrades to `outside > 0` and `predictive_heat`
    to `forecast > 2` -- so summer heat rejection engaged year-round.

    Removing the default fixes new entries only; the stored zero has to go
    explicitly. Zero was unreachable as a deliberate choice (the field was
    optional and the slider floor is 0, so leaving it alone produced the same
    value), and anyone who genuinely wants the lowest possible gate can enter
    1. Any other stored value is left untouched.
    """
    if entry.options.get(CONF_OUTSIDE_THRESHOLD) != 0:
        return True

    options = dict(entry.options)
    options.pop(CONF_OUTSIDE_THRESHOLD)
    _LOGGER.info(
        "Entry %s: removing outside temperature threshold of 0, which was "
        "written by a schema default rather than chosen. Summer mode is no "
        "longer gated on outdoor temperature for this cover; set a threshold "
        "in the climate options if you want one",
        entry.title or entry.entry_id,
    )
    hass.config_entries.async_update_entry(entry, options=options)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Adaptive Cover from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    coordinator = AdaptiveDataUpdateCoordinator(hass, entry)
    _temp_entity = entry.options.get(CONF_TEMP_ENTITY)
    _presence_entity = entry.options.get(CONF_PRESENCE_ENTITY)
    _weather_entity = entry.options.get(CONF_WEATHER_ENTITY)
    _cloud_entity = entry.options.get(CONF_CLOUD_COVERAGE_ENTITY)
    # window_entity may be a string (legacy single) or list (multi-select).
    _raw_window = entry.options.get(CONF_WINDOW_ENTITY) or []
    _window_entities = (
        [_raw_window] if isinstance(_raw_window, str) else list(_raw_window)
    )
    _cover_entities = entry.options.get(CONF_ENTITIES, [])
    _start_time_entity = entry.options.get(CONF_START_ENTITY)
    _end_time_entity = entry.options.get(CONF_END_ENTITY)
    _entities = ["sun.sun"]
    for entity in [
        _temp_entity,
        _presence_entity,
        _weather_entity,
        _cloud_entity,
        _start_time_entity,
        _end_time_entity,
    ]:
        if entity is not None:
            _entities.append(entity)
    _entities.extend(e for e in _window_entities if e)

    _LOGGER.debug("Setting up entry %s", entry.data.get("name"))

    entry.async_on_unload(
        async_track_state_change_event(
            hass,
            _entities,
            coordinator.async_check_entity_state_change,
        )
    )

    entry.async_on_unload(
        async_track_state_change_event(
            hass,
            _cover_entities,
            coordinator.async_check_cover_state_change,
        )
    )
    entry.async_on_unload(coordinator.async_cancel_scheduled_callbacks)

    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Defaulted: a failed setup can leave the entry absent, and an
        # unguarded pop turns that into a KeyError that masks the real error.
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        _release_shared_data(hass, entry)

    return unload_ok


def _release_shared_data(hass: HomeAssistant, unloading: ConfigEntry) -> None:
    """Drop shared cache records no remaining entry can use.

    The forecast cache is keyed by weather entity and shared across entries, so
    it cannot be dropped with the entry that happened to create it. Without
    this, every _ForecastCache and its asyncio.Lock outlived reloads and
    accumulated for the lifetime of the process.
    """
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        return

    still_wanted = {
        other.options.get(CONF_WEATHER_ENTITY)
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != unloading.entry_id
    }
    shared = domain_data.get(SHARED_DATA_KEY) or {}
    cache = shared.get(_FORECAST_CACHE_KEY) or {}
    for weather_entity in [key for key in cache if key not in still_wanted]:
        del cache[weather_entity]
        _LOGGER.debug("Released forecast cache for %s", weather_entity)

    if not cache:
        shared.pop(_FORECAST_CACHE_KEY, None)
    if not shared:
        domain_data.pop(SHARED_DATA_KEY, None)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
