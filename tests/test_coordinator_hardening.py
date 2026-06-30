"""Coordinator hardening regression tests."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.adaptive_cover.coordinator import (
    AdaptiveCoverManager,
    AdaptiveDataUpdateCoordinator,
    StateChangedData,
)

UTC = getattr(dt, "UTC", dt.timezone.utc)  # noqa: UP017


def _coordinator_shell() -> AdaptiveDataUpdateCoordinator:
    """Create a coordinator instance without running __init__."""
    return AdaptiveDataUpdateCoordinator.__new__(AdaptiveDataUpdateCoordinator)


def _run(coro):
    """Run async test helpers without pytest-asyncio."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _discard_coro(coro):
    """Consume coroutine objects passed into mocked async_create_task."""
    if asyncio.iscoroutine(coro):
        coro.close()


def test_update_manager_preserves_manual_state_before_restore() -> None:
    """Persisted manual state should survive startup until switches restore."""
    coordinator = _coordinator_shell()
    coordinator.manager = MagicMock()
    coordinator.manager.manual_controlled = ["cover.kitchen"]
    coordinator.entities = ["cover.kitchen"]
    coordinator._switches_restored = False
    coordinator._manual_toggle = None

    coordinator._update_manager_and_covers()

    coordinator.manager.add_covers.assert_called_once_with(["cover.kitchen"])
    coordinator.manager.reset.assert_not_called()


def test_update_manager_resets_only_when_manual_toggle_off_after_restore() -> None:
    """Manual-reset sweep should only happen after restore and toggle=off."""
    coordinator = _coordinator_shell()
    coordinator.manager = MagicMock()
    coordinator.manager.manual_controlled = ["cover.office"]
    coordinator.entities = ["cover.office"]
    coordinator._switches_restored = True
    coordinator._manual_toggle = False

    coordinator._update_manager_and_covers()

    coordinator.manager.reset.assert_called_once_with("cover.office")


def test_set_expected_switch_ids_empty_marks_restored() -> None:
    """Entries with no switches should not deadlock first refresh."""
    coordinator = _coordinator_shell()
    coordinator.expected_restore_ids = set()
    coordinator._switches_restored = False
    coordinator.logger = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.hass.async_create_task = MagicMock(side_effect=_discard_coro)
    coordinator.async_refresh = AsyncMock()

    coordinator.set_expected_switch_ids(set())

    assert coordinator._switches_restored is True
    coordinator.hass.async_create_task.assert_called_once()


def test_end_time_guard_handles_unparseable_string() -> None:
    """_end_time should return None when end_time cannot be parsed."""
    coordinator = _coordinator_shell()
    coordinator.end_time_entity = None
    coordinator.end_time = "not-a-time"

    with patch(
        "custom_components.adaptive_cover.coordinator.get_datetime_from_str",
        return_value=None,
    ):
        assert coordinator._end_time is None


def test_before_end_time_handles_aware_datetime() -> None:
    """before_end_time should not raise on tz-aware end-time values."""
    coordinator = _coordinator_shell()
    coordinator.logger = MagicMock()
    coordinator.end_time_entity = None
    coordinator.end_time = "22:00:00"

    with patch(
        "custom_components.adaptive_cover.coordinator.get_datetime_from_str",
        return_value=dt.datetime.now(UTC) + dt.timedelta(minutes=5),
    ):
        assert coordinator.before_end_time is True


def test_after_start_time_handles_aware_datetime() -> None:
    """after_start_time should compare safely against tz-aware start times."""
    coordinator = _coordinator_shell()
    coordinator.logger = MagicMock()
    coordinator.start_time_entity = None
    coordinator.start_time = "06:00:00"

    with patch(
        "custom_components.adaptive_cover.coordinator.get_datetime_from_str",
        return_value=dt.datetime.now(UTC) - dt.timedelta(minutes=5),
    ):
        assert coordinator.after_start_time is True


def test_process_entity_state_change_clears_target_with_tolerance() -> None:
    """Wait state should clear while preserving target for manager guard."""
    coordinator = _coordinator_shell()
    coordinator.logger = MagicMock()
    coordinator._cover_type = "cover_blind"
    coordinator.ignore_intermediate_states = False
    coordinator.manual_threshold = 5
    coordinator.wait_for_target = {"cover.kitchen": True}
    coordinator.target_call = {"cover.kitchen": 100}
    coordinator._wait_for_target_started_at = {"cover.kitchen": time.monotonic()}
    coordinator._WAIT_FOR_TARGET_TIMEOUT_S = 90
    coordinator.state_change_data = StateChangedData(
        "cover.kitchen",
        old_state=SimpleNamespace(state="open", attributes={"current_position": 90}),
        new_state=SimpleNamespace(state="open", attributes={"current_position": 99}),
    )

    coordinator.process_entity_state_change()

    assert coordinator.wait_for_target["cover.kitchen"] is False
    assert coordinator.target_call["cover.kitchen"] == 100
    assert "cover.kitchen" not in coordinator._wait_for_target_started_at


def test_process_entity_state_change_timeout_clears_stale_wait_state() -> None:
    """Stale wait_for_target should be cleared after timeout."""
    coordinator = _coordinator_shell()
    coordinator.logger = MagicMock()
    coordinator._cover_type = "cover_blind"
    coordinator.ignore_intermediate_states = False
    coordinator.manual_threshold = 1
    coordinator.wait_for_target = {"cover.office": True}
    coordinator.target_call = {"cover.office": 100}
    coordinator._wait_for_target_started_at = {"cover.office": time.monotonic() - 120}
    coordinator._WAIT_FOR_TARGET_TIMEOUT_S = 90
    coordinator.state_change_data = StateChangedData(
        "cover.office",
        old_state=SimpleNamespace(state="open", attributes={"current_position": 95}),
        new_state=SimpleNamespace(state="open", attributes={"current_position": 20}),
    )

    coordinator.process_entity_state_change()

    assert coordinator.wait_for_target["cover.office"] is False
    assert "cover.office" not in coordinator.target_call
    coordinator.logger.warning.assert_called_once()


def test_pos_sun_fallback_when_sun_entity_missing() -> None:
    """pos_sun should use safe fallback values when sun.sun is unavailable."""
    coordinator = _coordinator_shell()
    coordinator.logger = MagicMock()
    coordinator.hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _entity_id: None),
    )

    assert coordinator.pos_sun == [0.0, -90.0]


def test_manager_consumes_target_call_before_manual_detect() -> None:
    """Target guard should clear tracked target and skip manual detection."""
    manager = AdaptiveCoverManager.__new__(AdaptiveCoverManager)
    manager.covers = {"cover.kitchen"}
    manager.manual_control = {}
    manager.manual_control_time = {}
    manager.reset_duration = dt.timedelta(minutes=15)
    manager.logger = MagicMock()
    manager.mark_manual_control = MagicMock()
    manager.set_last_updated = MagicMock()

    event = StateChangedData(
        "cover.kitchen",
        old_state=SimpleNamespace(state="open", attributes={"current_position": 20}),
        new_state=SimpleNamespace(
            state="open",
            attributes={"current_position": 99},
            last_updated=SimpleNamespace(),
        ),
    )
    target_call = {"cover.kitchen": 100}

    manager.handle_state_change(
        states_data=event,
        our_state=40,
        blind_type="cover_blind",
        allow_reset=False,
        wait_target_call={"cover.kitchen": False},
        manual_threshold=5,
        target_call=target_call,
    )

    assert "cover.kitchen" not in target_call
    manager.mark_manual_control.assert_not_called()


def test_window_latch_listener_replaced_on_reschedule() -> None:
    """Scheduling a new latch release should cancel stale listener handles."""
    coordinator = _coordinator_shell()
    coordinator.logger = MagicMock()
    coordinator.window_open_hold = 300
    coordinator.window_entities = ["binary_sensor.kitchen_window"]
    stale_cancel = MagicMock()
    coordinator._window_latch_listeners = [stale_cancel]
    coordinator.hass = MagicMock()
    coordinator.async_refresh = AsyncMock()
    coordinator.state_change = False

    new_cancel = MagicMock()
    event = SimpleNamespace(
        data={
            "entity_id": "binary_sensor.kitchen_window",
            "old_state": SimpleNamespace(state="on"),
            "new_state": SimpleNamespace(state="off"),
        }
    )

    with patch(
        "custom_components.adaptive_cover.coordinator.async_track_point_in_time",
        return_value=new_cancel,
    ):
        _run(coordinator.async_check_entity_state_change(event))

    stale_cancel.assert_called_once()
    assert coordinator._window_latch_listeners == [new_cancel]
    coordinator.async_refresh.assert_awaited_once()


def test_forecast_failure_uses_short_retry_ttl() -> None:
    """Failures should use the short retry window, not the success cache TTL."""
    coordinator = _coordinator_shell()
    coordinator.hass = SimpleNamespace(
        states=SimpleNamespace(get=MagicMock(return_value=None))
    )
    coordinator.logger = MagicMock()
    coordinator._FORECAST_CACHE_TTL = dt.timedelta(minutes=15)
    coordinator._FORECAST_FAILURE_RETRY_TTL = dt.timedelta(seconds=60)
    coordinator._last_forecast_entity = "weather.home"
    coordinator._last_forecast_fetch = dt.datetime.now(UTC) - dt.timedelta(minutes=2)
    coordinator._last_forecast_success = False
    coordinator._max_forecast_temp = 25.0

    _run(coordinator._async_update_forecast_max("weather.home"))

    coordinator.hass.states.get.assert_called_once_with("weather.home")
    assert coordinator._max_forecast_temp is None
    assert coordinator._last_forecast_success is False
