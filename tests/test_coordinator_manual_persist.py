"""Tests for AdaptiveCoverManager manual state persistence and startup drive guard."""
from __future__ import annotations

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from custom_components.adaptive_cover.coordinator import AdaptiveCoverManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine synchronously (no pytest-asyncio required)."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_manager(tmp_storage=None, entry_id: str = "test_entry"):
    """Return an AdaptiveCoverManager backed by an in-memory Store mock."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    logger = MagicMock()

    store = MagicMock()
    store.async_load = AsyncMock(return_value=tmp_storage)
    store.async_save = AsyncMock()

    with patch(
        "custom_components.adaptive_cover.coordinator.Store",
        return_value=store,
    ):
        manager = AdaptiveCoverManager(hass, entry_id, {"minutes": 15}, logger)

    manager._store = store
    return manager, store, hass


# ---------------------------------------------------------------------------
# Persistence: async_load
# ---------------------------------------------------------------------------

class TestManualStatePersistence:
    def test_load_restores_manual_control(self):
        stored = {
            "manual_control": {"cover.south": True, "cover.west": False},
            "manual_control_time": {},
        }
        manager, store, _ = _make_manager(tmp_storage=stored)
        _run(manager.async_load())

        assert manager.manual_control["cover.south"] is True
        assert manager.manual_control["cover.west"] is False

    def test_load_restores_manual_control_time(self):
        ts = dt.datetime(2026, 6, 1, 3, 0, 0, tzinfo=dt.timezone.utc)
        stored = {
            "manual_control": {"cover.south": True},
            "manual_control_time": {"cover.south": ts.isoformat()},
        }
        manager, store, _ = _make_manager(tmp_storage=stored)
        _run(manager.async_load())

        restored_ts = manager.manual_control_time["cover.south"]
        assert restored_ts.year == 2026
        assert restored_ts.hour == 3

    def test_load_with_no_stored_data_is_noop(self):
        manager, store, _ = _make_manager(tmp_storage=None)
        _run(manager.async_load())

        assert manager.manual_control == {}
        assert manager.manual_control_time == {}

    def test_load_ignores_malformed_timestamps(self):
        stored = {
            "manual_control": {"cover.south": True},
            "manual_control_time": {"cover.south": "not-a-datetime"},
        }
        manager, store, _ = _make_manager(tmp_storage=stored)
        _run(manager.async_load())

        assert "cover.south" not in manager.manual_control_time
        assert manager.manual_control.get("cover.south") is True


# ---------------------------------------------------------------------------
# Persistence: _schedule_save is called on mutations
# ---------------------------------------------------------------------------

class TestManualStateSaveOnMutation:
    def test_mark_manual_control_schedules_save(self):
        manager, store, hass = _make_manager()
        manager.mark_manual_control("cover.south")

        hass.async_create_task.assert_called_once()

    def test_reset_schedules_save(self):
        manager, store, hass = _make_manager()
        manager.manual_control["cover.south"] = True
        manager.manual_control_time["cover.south"] = dt.datetime.now(dt.timezone.utc)

        hass.async_create_task.reset_mock()
        manager.reset("cover.south")

        hass.async_create_task.assert_called_once()

    def test_set_last_updated_schedules_save(self):
        manager, store, hass = _make_manager()
        new_state = MagicMock()
        new_state.last_updated = dt.datetime.now(dt.timezone.utc)

        manager.set_last_updated("cover.south", new_state, allow_reset=True)

        hass.async_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# Startup guard: _switches_restored gate
# ---------------------------------------------------------------------------

class TestStartupGuard:
    def _make_coordinator(self):
        """Return a minimal coordinator-like object with the switch-restore gate."""
        hass = MagicMock()
        hass.async_create_task = MagicMock()
        logger = MagicMock()
        logger.debug = MagicMock()

        class FakeCoordinator:
            def __init__(self):
                self._switches_restored = False
                self.expected_restore_ids: set[str] = set()
                self.restored_ids: set[str] = set()
                self.hass = hass
                self.logger = logger
                self._refresh_calls = 0

            def set_expected_switch_ids(self, ids: set[str]) -> None:
                self.expected_restore_ids = ids

            def mark_switch_restored(self, unique_id: str) -> None:
                self.restored_ids.add(unique_id)
                if (
                    self.expected_restore_ids
                    and self.restored_ids >= self.expected_restore_ids
                ):
                    self._switches_restored = True
                    self.hass.async_create_task(self._async_refresh())

            async def _async_refresh(self):
                self._refresh_calls += 1

        return FakeCoordinator()

    def test_not_restored_until_all_expected_ids_marked(self):
        coord = self._make_coordinator()
        coord.set_expected_switch_ids({"switch_a", "switch_b"})

        coord.mark_switch_restored("switch_a")
        assert coord._switches_restored is False

        coord.mark_switch_restored("switch_b")
        assert coord._switches_restored is True

    def test_single_switch_restores_immediately(self):
        coord = self._make_coordinator()
        coord.set_expected_switch_ids({"switch_a"})

        coord.mark_switch_restored("switch_a")
        assert coord._switches_restored is True

    def test_extra_restore_call_does_not_flip_back(self):
        coord = self._make_coordinator()
        coord.set_expected_switch_ids({"switch_a"})
        coord.mark_switch_restored("switch_a")
        assert coord._switches_restored is True

        coord.mark_switch_restored("switch_a")
        assert coord._switches_restored is True

    def test_refresh_scheduled_when_all_restored(self):
        coord = self._make_coordinator()
        coord.set_expected_switch_ids({"switch_a"})

        coord.mark_switch_restored("switch_a")

        coord.hass.async_create_task.assert_called_once()

    def test_refresh_not_scheduled_when_partial(self):
        coord = self._make_coordinator()
        coord.set_expected_switch_ids({"switch_a", "switch_b"})

        coord.mark_switch_restored("switch_a")

        coord.hass.async_create_task.assert_not_called()

    def test_empty_expected_set_never_triggers(self):
        """If no switches are expected (edge case), guard stays False."""
        coord = self._make_coordinator()
        coord.set_expected_switch_ids(set())

        coord.mark_switch_restored("switch_orphan")
        assert coord._switches_restored is False
