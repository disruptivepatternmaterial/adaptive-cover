"""Regression tests for the window/door safety interlock entry points."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.adaptive_cover.button import AdaptiveCoverButton
from custom_components.adaptive_cover.coordinator import (
    AdaptiveDataUpdateCoordinator,
    CoverCommandError,
)
from custom_components.adaptive_cover.switch import AdaptiveCoverSwitch


def _run(coro):
    """Run async test helpers without pytest-asyncio."""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_cover_command_error_is_home_assistant_native() -> None:
    """Entity actions must surface operational failures through HA's API."""
    assert issubclass(CoverCommandError, HomeAssistantError)


def _coordinator() -> MagicMock:
    """Return a coordinator mock with an active pano-door interlock."""
    coordinator = MagicMock()
    coordinator.is_window_open = True
    coordinator.state = 4
    coordinator.get_effective_state.return_value = 100
    coordinator.entities = ["cover.panoramic_door_center"]
    coordinator.manager.is_cover_manual.return_value = True
    coordinator.check_adaptive_time = False
    coordinator.async_set_position = AsyncMock()
    coordinator.async_refresh = AsyncMock()
    coordinator.wait_for_target = {"cover.panoramic_door_center": False}
    return coordinator


def _real_coordinator(
    *,
    cover_type: str = "cover_blind",
    door_state: str = "on",
    cover_position: int = 4,
) -> tuple[AdaptiveDataUpdateCoordinator, str]:
    """Build a real coordinator shell for command-dispatch tests."""
    cover = "cover.panoramic_door_center"
    door = "binary_sensor.pano_door"
    states = {
        door: SimpleNamespace(state=door_state, attributes={}),
        cover: SimpleNamespace(
            state="open", attributes={"current_position": cover_position}
        ),
    }
    coordinator = AdaptiveDataUpdateCoordinator.__new__(AdaptiveDataUpdateCoordinator)
    coordinator.hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda entity_id: states.get(entity_id)),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    coordinator.config_entry = SimpleNamespace(options={})
    coordinator.window_entities = [door]
    coordinator.window_open_hold = 300
    coordinator._last_window_open_ts = None
    coordinator._inverse_state = False
    coordinator._cover_type = cover_type
    coordinator.manual_threshold = 5
    coordinator.wait_for_target = {}
    coordinator.target_call = {}
    coordinator._wait_for_target_started_at = {}
    coordinator._command_timeout_listeners = {}
    coordinator.logger = MagicMock()
    return coordinator, cover


def test_reset_manual_button_cannot_close_cover_during_interlock() -> None:
    """Resetting a manual latch must resolve through the safe target."""
    coordinator = _coordinator()
    button = AdaptiveCoverButton.__new__(AdaptiveCoverButton)
    button.coordinator = coordinator
    button._config_entry = SimpleNamespace(options={})
    button._entities = ["cover.panoramic_door_center"]

    _run(button.async_press())

    coordinator.get_effective_state.assert_called_once_with(4, {}, window_open=True)
    coordinator.async_set_position.assert_awaited_once_with(
        "cover.panoramic_door_center", 100
    )
    coordinator.manager.reset.assert_called_once_with("cover.panoramic_door_center")


def test_enabling_control_cannot_close_cover_during_interlock() -> None:
    """Safety must bypass manual and schedule gates when control is enabled."""
    coordinator = _coordinator()
    switch = AdaptiveCoverSwitch.__new__(AdaptiveCoverSwitch)
    switch.coordinator = coordinator
    switch._key = "control_toggle"
    switch._config_entry = SimpleNamespace(options={})
    switch.schedule_update_ha_state = MagicMock()

    _run(switch.async_turn_on())

    coordinator.get_effective_state.assert_called_once_with(4, {}, window_open=True)
    coordinator.async_set_position.assert_awaited_once_with(
        "cover.panoramic_door_center", 100
    )
    coordinator.async_refresh.assert_awaited_once()


def test_final_dispatch_rechecks_interlock_after_stale_target() -> None:
    """A door opening after calculation must still replace a closing target."""
    coordinator, cover = _real_coordinator()

    _run(coordinator.async_set_manual_position(cover, 4))

    coordinator.hass.services.async_call.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {"entity_id": cover, "position": 100},
        blocking=True,
    )
    assert coordinator.target_call[cover] == 100


def test_inflight_safe_target_suppresses_intermediate_recommands() -> None:
    """Position ticks during an active open command must not reissue it."""
    coordinator, cover = _real_coordinator()
    coordinator.wait_for_target[cover] = True
    coordinator.target_call[cover] = 100

    _run(coordinator.async_set_manual_position(cover, 4))

    coordinator.hass.services.async_call.assert_not_awaited()


def test_safe_target_uses_same_tolerance_as_command_settle() -> None:
    """A shade accepted at 99% must not be commanded to 100% forever."""
    coordinator, cover = _real_coordinator(cover_position=99)

    assert coordinator.check_position(cover, 100) is False

    coordinator.hass.states.get = lambda entity_id: (
        SimpleNamespace(state="on", attributes={})
        if entity_id == "binary_sensor.pano_door"
        else SimpleNamespace(state="open", attributes={"current_position": 90})
    )
    assert coordinator.check_position(cover, 100) is True


def test_configured_max_position_always_bounds_interlock_target() -> None:
    """The sun-only applicability flag cannot disable a hardware-safe maximum."""
    coordinator, _cover = _real_coordinator()
    options = {"max_position": 60, "enable_max_position": False}

    assert coordinator._window_open_target(options) == 60

    coordinator._inverse_state = True
    assert coordinator._window_open_target(options) == 40


def test_tilt_interlock_uses_tilt_service() -> None:
    """Tilt entries must enforce the same safe target on the correct axis."""
    coordinator, cover = _real_coordinator(cover_type="cover_tilt")
    coordinator.hass.states.get = lambda entity_id: (
        SimpleNamespace(state="on", attributes={})
        if entity_id == "binary_sensor.pano_door"
        else SimpleNamespace(state="open", attributes={"current_tilt_position": 4})
    )

    _run(coordinator.async_set_manual_position(cover, 4))

    coordinator.hass.services.async_call.assert_awaited_once_with(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": cover, "tilt_position": 100},
        blocking=True,
    )


def test_interlock_attempts_every_cover_before_propagating_failure() -> None:
    """One unavailable motor must not leave the next doorway shade closed."""
    coordinator = AdaptiveDataUpdateCoordinator.__new__(AdaptiveDataUpdateCoordinator)
    coordinator._inverse_state = False
    coordinator.entities = ["cover.failed", "cover.available"]
    coordinator.window_entities = ["binary_sensor.pano_door"]
    coordinator.logger = MagicMock()
    coordinator.async_set_manual_position = AsyncMock(
        side_effect=[CoverCommandError("failed motor"), None]
    )

    with pytest.raises(CoverCommandError, match="failed motor"):
        _run(coordinator._async_drive_to_max_open({}))

    assert coordinator.async_set_manual_position.await_args_list == [
        (("cover.failed", 100),),
        (("cover.available", 100),),
    ]


def test_slow_service_times_out_and_next_cover_is_attempted() -> None:
    """A hung platform cannot block safety dispatch to the next motor."""
    coordinator, _cover = _real_coordinator()
    coordinator.entities = ["cover.failed", "cover.available"]
    coordinator._COVER_SERVICE_TIMEOUT_S = 0.001
    calls: list[str] = []

    async def _async_call(_domain, _service, service_data, **_kwargs):
        calls.append(service_data["entity_id"])
        if service_data["entity_id"] == "cover.failed":
            await asyncio.sleep(0.02)

    coordinator.hass.services.async_call = _async_call
    coordinator.hass.states.get = lambda entity_id: (
        SimpleNamespace(state="on", attributes={})
        if entity_id == "binary_sensor.pano_door"
        else SimpleNamespace(state="open", attributes={"current_position": 4})
    )

    with pytest.raises(CoverCommandError):
        _run(coordinator._async_drive_to_max_open({}))

    assert calls == ["cover.failed", "cover.available"]
    assert coordinator.wait_for_target["cover.failed"] is False
    assert coordinator.target_call["cover.available"] == 100


def test_wait_tracking_expires_without_another_cover_event() -> None:
    """A silent cover cannot leave command tracking active forever."""
    coordinator, cover = _real_coordinator()
    callbacks = []

    def _schedule(_hass, _delay, callback):
        callbacks.append(callback)
        return MagicMock()

    with patch(
        "custom_components.adaptive_cover.coordinator.async_call_later",
        side_effect=_schedule,
    ):
        _run(coordinator.async_set_manual_position(cover, 4))

    assert coordinator.wait_for_target[cover] is True
    assert len(callbacks) == 1

    callbacks[0](None)

    assert coordinator.wait_for_target[cover] is False
    assert coordinator.target_call[cover] == 100


def test_ordinary_drive_attempts_all_covers_after_one_failure() -> None:
    """Observable service failures must not widen to later covers."""
    coordinator = AdaptiveDataUpdateCoordinator.__new__(AdaptiveDataUpdateCoordinator)
    coordinator.window_entities = []
    coordinator._switches_restored = True
    coordinator._control_toggle = True
    coordinator.state_change = True
    coordinator.entities = ["cover.failed", "cover.available"]
    coordinator.logger = MagicMock()
    coordinator.async_handle_call_service = AsyncMock(
        side_effect=[CoverCommandError("failed motor"), None]
    )

    _run(coordinator.async_handle_state_change(40, options={}))

    assert [
        call.args[0] for call in coordinator.async_handle_call_service.await_args_list
    ] == [
        "cover.failed",
        "cover.available",
    ]
    assert coordinator.state_change is False


def test_startup_drive_attempts_all_covers_after_one_failure() -> None:
    """A failed startup command must not suppress later cover commands."""
    coordinator = AdaptiveDataUpdateCoordinator.__new__(AdaptiveDataUpdateCoordinator)
    coordinator.window_entities = []
    coordinator._switches_restored = True
    coordinator._control_toggle = True
    coordinator.first_refresh = True
    coordinator.entities = ["cover.failed", "cover.available"]
    coordinator.start_time_entity = None
    coordinator.start_time = None
    coordinator._start_time = None
    coordinator.end_time_entity = None
    coordinator.end_time = None
    coordinator.logger = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.manager.is_cover_manual.return_value = False
    coordinator.check_position_delta = MagicMock(return_value=True)
    coordinator.async_set_position = AsyncMock(
        side_effect=[CoverCommandError("failed motor"), None]
    )

    _run(coordinator.async_handle_first_refresh(40, options={}))

    assert [
        call.args[0] for call in coordinator.async_set_position.await_args_list
    ] == [
        "cover.failed",
        "cover.available",
    ]
    assert coordinator.first_refresh is False


def test_timed_drive_attempts_all_covers_after_one_failure() -> None:
    """A failed timed command must not suppress later cover commands."""
    coordinator = AdaptiveDataUpdateCoordinator.__new__(AdaptiveDataUpdateCoordinator)
    coordinator.window_entities = []
    coordinator._control_toggle = True
    coordinator._inverse_state = False
    coordinator.timed_refresh = True
    coordinator.entities = ["cover.failed", "cover.available"]
    coordinator.logger = MagicMock()
    coordinator.async_set_manual_position = AsyncMock(
        side_effect=[CoverCommandError("failed motor"), None]
    )

    _run(
        coordinator.async_handle_timed_refresh(
            {"sunset_position": 20}, window_open=False
        )
    )

    assert [
        call.args[0] for call in coordinator.async_set_manual_position.await_args_list
    ] == ["cover.failed", "cover.available"]
    assert coordinator.timed_refresh is False
