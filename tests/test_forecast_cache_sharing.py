"""Regression: one weather.get_forecasts call per weather entity, not per entry.

Config entries generally all name the same weather entity, and every
coordinator refreshes in the same event-loop tick, so a per-coordinator cache
multiplied the service call rate by the number of entries.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.adaptive_cover import coordinator as coordinator_mod
from custom_components.adaptive_cover.const import DOMAIN

WEATHER = "weather.test"
ENTRY_COUNT = 10
Coordinator = coordinator_mod.AdaptiveDataUpdateCoordinator


class _Hass:
    """Fake hass recording every service call."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.calls: list[dict] = []
        self.services = SimpleNamespace(async_call=self._async_call)
        self.states = SimpleNamespace(
            get=lambda eid: SimpleNamespace(state="cloudy", attributes={})
        )

    async def _async_call(self, domain, service, data, target=None, **_kwargs):
        self.calls.append({"domain": domain, "service": service, "type": data["type"]})
        await asyncio.sleep(0)
        return {
            target["entity_id"]: {
                "forecast": [{"datetime": "2026-08-27T00:00:00", "temperature": 81.0}]
            }
        }


class _Entry:
    """Minimal stand-in exposing only what the forecast path touches."""

    _FORECAST_CACHE_TTL = Coordinator._FORECAST_CACHE_TTL
    _FORECAST_FAILURE_RETRY_TTL = Coordinator._FORECAST_FAILURE_RETRY_TTL
    _async_update_forecast_max = Coordinator._async_update_forecast_max
    _async_fetch_forecast_max = Coordinator._async_fetch_forecast_max

    def __init__(self, hass: _Hass) -> None:
        self.hass = hass
        self.logger = MagicMock()
        self._max_forecast_temp = None


async def _burst(entries) -> None:
    await asyncio.gather(*(e._async_update_forecast_max(WEATHER) for e in entries))


def _run(coro):
    """Run a coroutine without disturbing the suite's ambient event loop.

    asyncio.run() clears the current event loop when it finishes, and other
    modules in this suite reach for it through asyncio.get_event_loop().
    """
    try:
        previous = asyncio.get_event_loop()
    except RuntimeError:
        previous = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(previous)


def _cache(hass: _Hass):
    return hass.data[DOMAIN][coordinator_mod._FORECAST_CACHE_KEY][WEATHER]


def test_simultaneous_refreshes_coalesce_into_one_call() -> None:
    """Ten entries refreshing in one tick must produce one service call."""

    async def scenario():
        hass = _Hass()
        entries = [_Entry(hass) for _ in range(ENTRY_COUNT)]
        await _burst(entries)
        return hass, entries

    hass, entries = _run(scenario())
    assert len(hass.calls) == 1
    assert hass.calls[0] == {
        "domain": "weather",
        "service": "get_forecasts",
        "type": "daily",
    }
    assert [e._max_forecast_temp for e in entries] == [81.0] * ENTRY_COUNT


def test_cache_is_served_inside_the_ttl_and_refetched_after_it() -> None:
    """The shared cache keeps the existing 15-minute TTL semantics."""

    async def scenario():
        hass = _Hass()
        entries = [_Entry(hass) for _ in range(ENTRY_COUNT)]
        await _burst(entries)
        first = len(hass.calls)
        await _burst(entries)
        inside_ttl = len(hass.calls)
        _cache(hass).fetched_at -= dt.timedelta(minutes=16)
        await _burst(entries)
        return first, inside_ttl, len(hass.calls), entries

    first, inside_ttl, after_expiry, entries = _run(scenario())
    assert (first, inside_ttl, after_expiry) == (1, 1, 2)
    assert [e._max_forecast_temp for e in entries] == [81.0] * ENTRY_COUNT


def test_cache_lives_on_hass_not_on_the_coordinator() -> None:
    """A per-coordinator cache is what caused the fan-out."""

    async def scenario():
        hass = _Hass()
        entry = _Entry(hass)
        await entry._async_update_forecast_max(WEATHER)
        return hass

    hass = _run(scenario())
    assert coordinator_mod._FORECAST_CACHE_KEY in hass.data[DOMAIN]
    assert _cache(hass).value == 81.0
    assert _cache(hass).success is True


def test_missing_or_unknown_weather_entity_clears_the_value() -> None:
    """No entity, or an entity with no state, must not leave stale data."""

    async def scenario():
        hass = _Hass()
        entry = _Entry(hass)
        entry._max_forecast_temp = 99.0
        await entry._async_update_forecast_max(None)
        no_entity = entry._max_forecast_temp

        hass.states = SimpleNamespace(get=lambda eid: None)
        entry._max_forecast_temp = 99.0
        await entry._async_update_forecast_max(WEATHER)
        return no_entity, entry._max_forecast_temp, len(hass.calls)

    no_entity, no_state, calls = _run(scenario())
    assert no_entity is None
    assert no_state is None
    assert calls == 0
