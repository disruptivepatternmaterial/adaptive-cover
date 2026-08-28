"""Regression: command tracking must survive a failed drive and startup order.

The coordinator marks a cover as "we are driving this" before awaiting the
cover service, because Home Assistant can deliver the resulting state-change
event while that await is suspended. Two ways that went wrong:

* the service call could raise and leave the mark set, so for the next 90
  seconds a genuine manual move was attributed to the integration and did not
  latch manual override;
* a cover event arriving before the switches were restored reached the
  wait/target logic and consumed the mark, which
  docs/specs/behavioral-contract.md already forbids in writing.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import MagicMock

from custom_components.adaptive_cover.coordinator import AdaptiveDataUpdateCoordinator


def _run(coro):
    """Run async test helpers without pytest-asyncio."""
    return asyncio.get_event_loop().run_until_complete(coro)


COVER = "cover.library_shade"


def _coordinator(*, restored: bool = True, service_raises: bool = False):
    """Build a coordinator shell with only the command-tracking surface wired."""
    c = AdaptiveDataUpdateCoordinator.__new__(AdaptiveDataUpdateCoordinator)
    c.logger = MagicMock()
    c.hass = MagicMock()
    c.wait_for_target = {}
    c.target_call = {}
    c._wait_for_target_started_at = {}
    c._switches_restored = restored
    c._cover_type = "cover_blind"
    c.entities = [COVER]
    c.ignore_intermediate_states = False
    c.cover_state_change = False
    c.state_change_data = None
    c.manual_reset = 15

    async def _call(*_args, **_kwargs):
        if service_raises:
            raise RuntimeError("cover.set_cover_position failed: entity unavailable")

    c.hass.services.async_call = _call
    c.check_position = lambda _entity, _state: True
    return c


# --- a failed drive must not leave the cover marked as integration-driven ---


def test_a_failed_service_call_clears_command_tracking() -> None:
    """Otherwise a manual move within the 90 s window is misattributed to us."""
    c = _coordinator(service_raises=True)

    # The caller still sees the failure; only the tracking changes.
    with contextlib.suppress(RuntimeError):
        _run(c.async_set_manual_position(COVER, 60))

    assert c.wait_for_target.get(COVER) is not True
    assert COVER not in c.target_call
    assert COVER not in c._wait_for_target_started_at


def test_a_failed_service_call_still_propagates() -> None:
    """The rollback must not turn a failed drive into a silent success."""
    c = _coordinator(service_raises=True)

    raised = False
    try:
        _run(c.async_set_manual_position(COVER, 60))
    except RuntimeError:
        raised = True

    assert raised, "swallowing the error would hide a broken cover integration"


def test_a_successful_drive_still_records_the_command() -> None:
    """The mark has to be set before the await, and survive it."""
    c = _coordinator()

    _run(c.async_set_manual_position(COVER, 60))

    assert c.wait_for_target[COVER] is True
    assert c.target_call[COVER] == 60
    assert COVER in c._wait_for_target_started_at


# --- cover events before switch restore must not consume the mark -----------


def _cover_event(entity: str, old: str, new: str):
    """Build a cover state-change event as the coordinator reads it."""
    old_state = MagicMock()
    old_state.state = old
    new_state = MagicMock()
    new_state.state = new
    event = MagicMock()
    event.data = {
        "entity_id": entity,
        "old_state": old_state,
        "new_state": new_state,
    }
    return event


def test_a_cover_event_before_switch_restore_keeps_command_tracking() -> None:
    """docs/specs/behavioral-contract.md states this; the code did not enforce it."""
    c = _coordinator(restored=False)
    c.wait_for_target[COVER] = True
    c.target_call[COVER] = 60
    c.async_refresh = _noop
    c.process_entity_state_change = MagicMock()

    _run(c.async_check_cover_state_change(_cover_event(COVER, "50", "60")))

    c.process_entity_state_change.assert_not_called()
    assert c.wait_for_target[COVER] is True
    assert c.target_call[COVER] == 60


def test_a_cover_event_before_switch_restore_still_refreshes() -> None:
    """Position is worth observing during startup, just not interpreting."""
    c = _coordinator(restored=False)
    refreshed = []

    async def _refresh():
        refreshed.append(True)

    c.async_refresh = _refresh
    c.process_entity_state_change = MagicMock()

    _run(c.async_check_cover_state_change(_cover_event(COVER, "50", "60")))

    assert refreshed == [True]


def test_a_cover_event_after_switch_restore_is_processed() -> None:
    """Guard the gate: once restored, events must reach the manual-detect path."""
    c = _coordinator(restored=True)
    c.async_refresh = _noop
    c.process_entity_state_change = MagicMock()

    _run(c.async_check_cover_state_change(_cover_event(COVER, "50", "60")))

    c.process_entity_state_change.assert_called_once()


async def _noop():
    return None
