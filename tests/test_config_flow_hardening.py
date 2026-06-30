"""Config flow hardening regression tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.adaptive_cover.config_flow import ConfigFlowHandler, OptionsFlowHandler
from custom_components.adaptive_cover.const import (
    CONF_CLIMATE_MODE,
    CONF_ENABLE_MAX_POSITION,
    CONF_ENABLE_MIN_POSITION,
    CONF_END_ENTITY,
    CONF_END_TIME,
    CONF_MODE,
    CONF_RETURN_SUNSET,
    CONF_SENSOR_TYPE,
    CONF_WINDOW_ENTITY,
    CONF_WINDOW_OPEN_HOLD,
    SensorType,
)


def _run(coro):
    """Run async test helpers without pytest-asyncio."""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_async_step_update_preserves_collected_options() -> None:
    """Initial create_entry should include all collected option keys."""
    flow = ConfigFlowHandler()
    flow.type_blind = SensorType.BLIND
    flow.config = {
        "name": "Kitchen",
        CONF_MODE: SensorType.BLIND,
        CONF_CLIMATE_MODE: True,
        CONF_END_TIME: "22:00:00",
        CONF_END_ENTITY: "input_datetime.cover_end",
        CONF_RETURN_SUNSET: True,
        CONF_WINDOW_ENTITY: ["binary_sensor.window"],
        CONF_WINDOW_OPEN_HOLD: 180,
        CONF_ENABLE_MAX_POSITION: True,
        CONF_ENABLE_MIN_POSITION: False,
    }

    result = _run(flow.async_step_update())
    options = result["options"]

    assert result["type"] == "create_entry"
    assert options[CONF_END_TIME] == "22:00:00"
    assert options[CONF_END_ENTITY] == "input_datetime.cover_end"
    assert options[CONF_RETURN_SUNSET] is True
    assert options[CONF_WINDOW_ENTITY] == ["binary_sensor.window"]
    assert options[CONF_WINDOW_OPEN_HOLD] == 180
    assert options[CONF_ENABLE_MAX_POSITION] is True
    assert options[CONF_ENABLE_MIN_POSITION] is False


def test_async_step_user_sets_unique_id_and_routes_by_mode() -> None:
    """User step should set unique_id and route to the selected cover flow."""
    flow = ConfigFlowHandler()
    flow.async_step_vertical = AsyncMock(return_value={"type": "vertical"})

    result = _run(flow.async_step_user({"name": "Office", CONF_MODE: SensorType.BLIND}))

    assert result == {"type": "vertical"}
    assert flow._unique_id == "office_cover_blind"


def test_async_step_user_normalizes_name_in_unique_id() -> None:
    """Unique IDs should normalize whitespace in user-provided names."""
    flow = ConfigFlowHandler()
    flow.async_step_vertical = AsyncMock(return_value={"type": "vertical"})

    result = _run(
        flow.async_step_user({"name": "  Office   South  ", CONF_MODE: SensorType.BLIND})
    )

    assert result == {"type": "vertical"}
    assert flow._unique_id == "office_south_cover_blind"


def test_options_flow_handles_missing_legacy_keys() -> None:
    """Options init should not KeyError on entries missing newer keys."""
    config_entry = SimpleNamespace(
        data={CONF_SENSOR_TYPE: SensorType.BLIND},
        options={},
    )
    flow = OptionsFlowHandler(config_entry)

    result = _run(flow.async_step_init())

    assert result["type"] == "menu"
    assert "automation" in result["menu_options"]
    assert "blind" in result["menu_options"]
