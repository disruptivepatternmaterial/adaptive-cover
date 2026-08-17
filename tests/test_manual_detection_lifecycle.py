"""Manual-detection lifecycle tests replaying production event sequences.

Each scenario reproduces a real event sequence pulled from the BowmanMtn
recorder during the 2026-07-07 investigation of frozen sun tracking.
"""

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
from custom_components.adaptive_cover.select import total_minutes_from_duration

UTC = getattr(dt, "UTC", dt.timezone.utc)  # noqa: UP017


def _run(coro):
    """Run async test helpers without pytest-asyncio."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _coordinator_shell() -> AdaptiveDataUpdateCoordinator:
    """Create a coordinator instance without running __init__."""
    return AdaptiveDataUpdateCoordinator.__new__(AdaptiveDataUpdateCoordinator)


def _make_manager(reset_duration=None):
    """Return an AdaptiveCoverManager backed by an in-memory Store mock."""
    hass = MagicMock()
    hass.async_create_task = MagicMock(
        side_effect=lambda coro: coro.close() if asyncio.iscoroutine(coro) else None
    )
    logger = MagicMock()
    store = MagicMock()
    store.async_load = AsyncMock(return_value=None)
    store.async_save = AsyncMock()
    with patch(
        "custom_components.adaptive_cover.coordinator.Store",
        return_value=store,
    ):
        manager = AdaptiveCoverManager(
            hass, "test_entry", reset_duration or {"minutes": 15}, logger
        )
    manager.add_covers(["cover.library_shades"])
    return manager


def _cover_state(state: str, position: int | None) -> SimpleNamespace:
    attributes = {} if position is None else {"current_position": position}
    return SimpleNamespace(
        state=state,
        attributes=attributes,
        last_updated=dt.datetime.now(UTC),
    )


def _cover_event(entity_id: str, old, new) -> SimpleNamespace:
    return SimpleNamespace(
        data={"entity_id": entity_id, "old_state": old, "new_state": new}
    )


# ---------------------------------------------------------------------------
# Scenario 1: availability blip (library shade, 2026-07-07 08:37:41 MDT)
# Cover reconnects reporting position 100 while calculated state is 20.
# Must NOT enter manual detection.
# ---------------------------------------------------------------------------


class TestAvailabilityBlip:
    """unavailable/unknown transitions are availability events, not movement."""

    def _shell(self):
        coordinator = _coordinator_shell()
        coordinator.logger = MagicMock()
        coordinator.async_refresh = AsyncMock()
        return coordinator

    def test_return_from_unavailable_not_manual(self):
        """The exact production failure: unavailable -> open(100), calc 20."""
        coordinator = self._shell()
        coordinator.cover_state_change = False
        event = _cover_event(
            "cover.library_shades",
            old=_cover_state("unavailable", None),
            new=_cover_state("open", 100),
        )

        _run(coordinator.async_check_cover_state_change(event))

        assert coordinator.cover_state_change is False
        coordinator.async_refresh.assert_not_called()

    def test_drop_to_unavailable_not_manual(self):
        """A cover dropping offline is not a manual move."""
        coordinator = self._shell()
        coordinator.cover_state_change = False
        event = _cover_event(
            "cover.library_shades",
            old=_cover_state("open", 100),
            new=_cover_state("unavailable", None),
        )

        _run(coordinator.async_check_cover_state_change(event))

        assert coordinator.cover_state_change is False

    def test_unknown_old_state_not_manual(self):
        """Startup unknown->known transitions are not manual moves."""
        coordinator = self._shell()
        coordinator.cover_state_change = False
        event = _cover_event(
            "cover.library_shades",
            old=_cover_state("unknown", None),
            new=_cover_state("open", 100),
        )

        _run(coordinator.async_check_cover_state_change(event))

        assert coordinator.cover_state_change is False

    def test_normal_movement_still_processed(self):
        """Real position changes must still flow into manual detection."""
        coordinator = self._shell()
        coordinator.cover_state_change = False
        coordinator._cover_type = "cover_blind"
        coordinator.ignore_intermediate_states = False
        coordinator.wait_for_target = {}
        coordinator.target_call = {}
        coordinator._wait_for_target_started_at = {}
        event = _cover_event(
            "cover.library_shades",
            old=_cover_state("open", 100),
            new=_cover_state("open", 40),
        )

        _run(coordinator.async_check_cover_state_change(event))

        assert coordinator.cover_state_change is True
        coordinator.async_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# Scenario 2: slow group traverse (library group of 3 shades, > 90 s)
# Timeout fires mid-travel; the eventual settle at the commanded target
# must NOT be classified as manual.
# ---------------------------------------------------------------------------


class TestSlowTraverse:
    """Timeout keeps the commanded-target exemption for slow covers."""

    def test_settle_after_timeout_not_manual(self):
        """Settle at the commanded target after timeout is not manual."""
        coordinator = _coordinator_shell()
        coordinator.logger = MagicMock()
        coordinator._cover_type = "cover_blind"
        coordinator.ignore_intermediate_states = False
        coordinator.manual_threshold = 10
        coordinator.wait_for_target = {"cover.library_shades": True}
        coordinator.target_call = {"cover.library_shades": 100}
        coordinator._wait_for_target_started_at = {
            "cover.library_shades": time.monotonic() - 120
        }
        coordinator._WAIT_FOR_TARGET_TIMEOUT_S = 90

        # Mid-travel report at 55 arrives after the 90s timeout.
        coordinator.state_change_data = StateChangedData(
            "cover.library_shades",
            old_state=_cover_state("opening", 40),
            new_state=_cover_state("opening", 55),
        )
        coordinator.process_entity_state_change()

        assert coordinator.wait_for_target["cover.library_shades"] is False
        # The exemption survives the timeout.
        assert coordinator.target_call["cover.library_shades"] == 100

        # The cover finally settles at the commanded target; the manager
        # must consume the target instead of marking manual.
        manager = _make_manager()
        settle = StateChangedData(
            "cover.library_shades",
            old_state=_cover_state("opening", 90),
            new_state=_cover_state("open", 100),
        )
        manager.handle_state_change(
            settle,
            our_state=20,  # calculated sun position differs from the drive target
            blind_type="cover_blind",
            allow_reset=False,
            wait_target_call=coordinator.wait_for_target,
            manual_threshold=10,
            target_call=coordinator.target_call,
        )

        assert manager.is_cover_manual("cover.library_shades") is False
        assert "cover.library_shades" not in coordinator.target_call


# ---------------------------------------------------------------------------
# Scenario 3: genuine external move (bedtime automation closing to 0)
# MUST be classified as manual, and must log at INFO.
# ---------------------------------------------------------------------------


class TestGenuineExternalMove:
    """Real external drives are still detected as manual control."""

    def test_external_drive_marks_manual_and_logs(self):
        """A drive with no pending integration target is manual."""
        manager = _make_manager(reset_duration={"hours": 12, "minutes": 0})
        event = StateChangedData(
            "cover.library_shades",
            old_state=_cover_state("open", 100),
            new_state=_cover_state("closed", 0),
        )

        manager.handle_state_change(
            event,
            our_state=100,
            blind_type="cover_blind",
            allow_reset=False,
            wait_target_call={},
            manual_threshold=10,
            target_call={},
        )

        assert manager.is_cover_manual("cover.library_shades") is True
        manager.logger.info.assert_called()

    def test_user_stop_during_wait_marks_manual(self):
        """A settled position that is not the commanded target is manual."""
        manager = _make_manager()
        wait = {"cover.library_shades": True}
        target_call = {"cover.library_shades": 100}
        event = StateChangedData(
            "cover.library_shades",
            old_state=_cover_state("opening", 40),
            new_state=_cover_state("open", 19),
        )

        manager.handle_state_change(
            event,
            our_state=100,
            blind_type="cover_blind",
            allow_reset=False,
            wait_target_call=wait,
            manual_threshold=10,
            target_call=target_call,
        )

        assert manager.is_cover_manual("cover.library_shades") is True
        assert wait["cover.library_shades"] is False
        assert "cover.library_shades" not in target_call

    def test_open_to_open_tick_during_wait_is_not_manual(self):
        """Position ticks that stay open during a commanded drive are not a stop."""
        manager = _make_manager()
        wait = {"cover.library_shades": True}
        target_call = {"cover.library_shades": 100}
        event = StateChangedData(
            "cover.library_shades",
            old_state=_cover_state("open", 30),
            new_state=_cover_state("open", 40),
        )

        manager.handle_state_change(
            event,
            our_state=20,
            blind_type="cover_blind",
            allow_reset=False,
            wait_target_call=wait,
            manual_threshold=10,
            target_call=target_call,
        )

        assert manager.is_cover_manual("cover.library_shades") is False
        assert wait["cover.library_shades"] is True
        assert target_call["cover.library_shades"] == 100

    def test_timeout_opening_event_does_not_latch(self):
        """After wait times out, an opening report must not consume the target."""
        manager = _make_manager()
        wait = {"cover.library_shades": False}
        target_call = {"cover.library_shades": 100}
        event = StateChangedData(
            "cover.library_shades",
            old_state=_cover_state("opening", 40),
            new_state=_cover_state("opening", 55),
        )

        manager.handle_state_change(
            event,
            our_state=20,
            blind_type="cover_blind",
            allow_reset=False,
            wait_target_call=wait,
            manual_threshold=10,
            target_call=target_call,
        )

        assert manager.is_cover_manual("cover.library_shades") is False
        assert target_call["cover.library_shades"] == 100

    def test_mid_travel_during_wait_is_not_manual(self):
        """opening/closing reports while wait is active must not latch."""
        manager = _make_manager()
        wait = {"cover.library_shades": True}
        target_call = {"cover.library_shades": 100}
        event = StateChangedData(
            "cover.library_shades",
            old_state=_cover_state("closed", 0),
            new_state=_cover_state("opening", 40),
        )

        manager.handle_state_change(
            event,
            our_state=100,
            blind_type="cover_blind",
            allow_reset=False,
            wait_target_call=wait,
            manual_threshold=10,
            target_call=target_call,
        )

        assert manager.is_cover_manual("cover.library_shades") is False
        assert wait["cover.library_shades"] is True
        assert target_call["cover.library_shades"] == 100

    def test_reset_logs_resume(self):
        """Clearing a latched cover logs at INFO."""
        manager = _make_manager()
        manager.manual_control["cover.library_shades"] = True

        manager.reset("cover.library_shades")

        assert manager.is_cover_manual("cover.library_shades") is False
        manager.logger.info.assert_called()

    def test_reset_of_nonmanual_cover_is_silent(self):
        """Resetting an already-clear cover must not spam the log."""
        manager = _make_manager()
        manager.manual_control["cover.library_shades"] = False

        manager.reset("cover.library_shades")

        manager.logger.info.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 4: settle near target, later drift beyond threshold
# ---------------------------------------------------------------------------


class TestSettleThenDrift:
    """Settle within tolerance is exempt; later drift is a manual move."""

    def test_settle_within_tolerance_then_drift_marks_manual(self):
        """Tolerance settle consumes the target; later drift is manual."""
        manager = _make_manager()
        target_call = {"cover.library_shades": 100}

        # Settle 2% off target: exempt, target consumed.
        settle = StateChangedData(
            "cover.library_shades",
            old_state=_cover_state("opening", 90),
            new_state=_cover_state("open", 98),
        )
        manager.handle_state_change(
            settle,
            our_state=20,
            blind_type="cover_blind",
            allow_reset=False,
            wait_target_call={},
            manual_threshold=10,
            target_call=target_call,
        )
        assert manager.is_cover_manual("cover.library_shades") is False
        assert "cover.library_shades" not in target_call

        # Later external move beyond threshold: manual.
        drift = StateChangedData(
            "cover.library_shades",
            old_state=_cover_state("open", 98),
            new_state=_cover_state("open", 40),
        )
        manager.handle_state_change(
            drift,
            our_state=98,
            blind_type="cover_blind",
            allow_reset=False,
            wait_target_call={},
            manual_threshold=10,
            target_call=target_call,
        )
        assert manager.is_cover_manual("cover.library_shades") is True


# ---------------------------------------------------------------------------
# Scenario 5: duration select honesty (live entries store hours-based dicts)
# ---------------------------------------------------------------------------


class TestDurationSelectHonesty:
    """Hours-based durations display no option and are never rewritten."""

    def test_total_minutes_from_hours_dict(self):
        """Hours/days-based duration dicts sum to total minutes."""
        assert (
            total_minutes_from_duration({"hours": 3, "minutes": 0, "seconds": 0}) == 180
        )
        assert (
            total_minutes_from_duration({"hours": 12, "minutes": 0, "seconds": 0})
            == 720
        )
        assert total_minutes_from_duration({"minutes": 15}) == 15
        assert total_minutes_from_duration({"days": 1, "minutes": 0}) == 1440

    def test_legacy_sunset_sentinel_maps_to_240(self):
        """Legacy 9999-minute sentinel maps to 240 minutes."""
        assert total_minutes_from_duration({"minutes": 9999}) == 240

    def test_garbage_returns_none(self):
        """Unusable duration values return None instead of raising."""
        assert total_minutes_from_duration(None) is None
        assert total_minutes_from_duration({"minutes": "abc"}) is None

    def test_hours_duration_shows_no_option_and_keeps_options(self):
        """A 3-hour duration shows no option and is not rewritten."""
        from custom_components.adaptive_cover.select import (
            AdaptiveCoverOverrideSelect,
        )

        coordinator = MagicMock()
        config_entry = MagicMock()
        stored = {"manual_override_duration": {"hours": 3, "minutes": 0, "seconds": 0}}
        config_entry.options = dict(stored)
        config_entry.entry_id = "test_entry"

        entity = AdaptiveCoverOverrideSelect(coordinator, config_entry)

        # A 3-hour duration must not display as "none" (the old bug) nor
        # as any other option; and construction must not touch options.
        assert entity._attr_current_option is None
        assert config_entry.options == stored

    def test_minutes_duration_shows_matching_option(self):
        """A minutes-based duration maps to its select option."""
        from custom_components.adaptive_cover.select import (
            AdaptiveCoverOverrideSelect,
        )

        coordinator = MagicMock()
        config_entry = MagicMock()
        config_entry.options = {"manual_override_duration": {"minutes": 30}}
        config_entry.entry_id = "test_entry"

        entity = AdaptiveCoverOverrideSelect(coordinator, config_entry)

        assert entity._attr_current_option == "30_min"
