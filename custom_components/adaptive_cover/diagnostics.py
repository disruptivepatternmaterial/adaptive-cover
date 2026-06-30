"""Adaptive Cover integration diagnostics."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.diagnostics import async_redact_data

from .const import (
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_END_ENTITY,
    CONF_ENTITIES,
    CONF_IRRADIANCE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_START_ENTITY,
    CONF_TEMP_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_ENTITY,
    DOMAIN,
)

TO_REDACT = {
    CONF_ENTITIES,
    CONF_TEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_LUX_ENTITY,
    CONF_IRRADIANCE_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_WINDOW_ENTITY,
    CONF_START_ENTITY,
    CONF_END_ENTITY,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
):
    """Return config entry diagnostics."""
    coordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    runtime = None
    if coordinator is not None:
        wait_for_target = dict(coordinator.wait_for_target)
        target_call = dict(coordinator.target_call)
        runtime = {
            "last_update_success": coordinator.last_update_success,
            "switches_restored": coordinator._switches_restored,
            "control_method": coordinator.control_method,
            "manual_controlled_count": len(coordinator.manager.manual_controlled),
            "window_open": coordinator.is_window_open,
            "wait_for_target_count": len(wait_for_target),
            "wait_for_target_active_count": sum(
                1 for is_waiting in wait_for_target.values() if is_waiting
            ),
            "target_call_count": len(target_call),
        }

    return {
        "title": "Adaptive Cover Configuration",
        "type": "config_entry",
        "identifier": config_entry.entry_id,
        "config_data": async_redact_data(config_entry.data, TO_REDACT),
        "config_options": async_redact_data(config_entry.options, TO_REDACT),
        "runtime": runtime,
    }
