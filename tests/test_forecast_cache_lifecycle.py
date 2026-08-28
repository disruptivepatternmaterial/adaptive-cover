"""Regression: a cancelled or mistimed forecast fetch must not poison the cache.

The forecast cache is shared by every config entry pointing at the same weather
entity, so a single bad entry is served to all of them until its TTL expires.
Two ways that went wrong, plus the twice_daily selection reading the wrong day.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover import coordinator as coordinator_mod
from custom_components.adaptive_cover.const import DOMAIN

WEATHER = "weather.test"
Coordinator = coordinator_mod.AdaptiveDataUpdateCoordinator

PACIFIC = dt.timezone(dt.timedelta(hours=-7))
# 21:30 Pacific is already the next day in UTC. Fixed in the past so the
# machine's own date can never coincide with it.
LOCAL_EVENING = dt.datetime(2019, 6, 15, 21, 30, tzinfo=PACIFIC)


@pytest.fixture(autouse=True)
def ha_local_evening(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin Home Assistant's clock to a local evening that is tomorrow in UTC."""
    monkeypatch.setattr(coordinator_mod.dt_util, "now", lambda: LOCAL_EVENING)


class _Hass:
    """Fake hass whose forecast service is supplied per test."""

    def __init__(self, responder) -> None:  # noqa: ANN001
        self.data: dict = {}
        self.calls: list[str] = []
        self._responder = responder
        self.services = SimpleNamespace(async_call=self._async_call)
        self.states = SimpleNamespace(
            get=lambda eid: SimpleNamespace(state="sunny", attributes={})
        )

    async def _async_call(self, domain, service, data, target=None, **_kwargs):  # noqa: ANN001, ANN202
        self.calls.append(data["type"])
        return await self._responder(data["type"])


class _Entry:
    """Minimal stand-in exposing only the forecast path."""

    _FORECAST_CACHE_TTL = Coordinator._FORECAST_CACHE_TTL
    _FORECAST_FAILURE_RETRY_TTL = Coordinator._FORECAST_FAILURE_RETRY_TTL
    _FORECAST_FETCH_TIMEOUT_S = Coordinator._FORECAST_FETCH_TIMEOUT_S
    _async_update_forecast_max = Coordinator._async_update_forecast_max
    _async_fetch_forecast_max = Coordinator._async_fetch_forecast_max

    def __init__(self, hass: _Hass) -> None:
        self.hass = hass
        self.logger = MagicMock()
        self._max_forecast_temp = None


def _run(coro):
    """Run a coroutine without disturbing the suite's ambient event loop."""
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
    return hass.data[DOMAIN][coordinator_mod.SHARED_DATA_KEY][
        coordinator_mod._FORECAST_CACHE_KEY
    ][WEATHER]


def _daily(temperature: float):
    async def _responder(forecast_type: str):
        if forecast_type != "daily":
            return {}
        return {
            WEATHER: {
                "forecast": [
                    {"datetime": "2019-06-15T00:00:00", "temperature": temperature}
                ]
            }
        }

    return _responder


def test_cancellation_mid_fetch_does_not_poison_the_cache() -> None:
    """A reload cancelling the fetch must leave the entry refetchable.

    With fetched_at stamped before the call, the half-finished entry looked
    like an authoritative "no forecast" and was served to every config entry
    sharing this weather entity until the retry TTL expired.
    """

    async def scenario():
        async def _cancelled(_forecast_type):
            raise asyncio.CancelledError

        hass = _Hass(_cancelled)
        entry = _Entry(hass)
        with pytest.raises(asyncio.CancelledError):
            await entry._async_update_forecast_max(WEATHER)
        return hass

    hass = _run(scenario())
    assert _cache(hass).fetched_at is None, "cancelled fetch left a servable entry"
    assert _cache(hass).success is False


def test_a_later_refresh_refetches_after_a_cancellation() -> None:
    """The entry left by a cancellation must not satisfy the TTL check."""

    async def scenario():
        state = {"cancel": True}

        async def _responder(forecast_type: str):
            if state["cancel"]:
                raise asyncio.CancelledError
            return await _daily(81.0)(forecast_type)

        hass = _Hass(_responder)
        entry = _Entry(hass)
        with pytest.raises(asyncio.CancelledError):
            await entry._async_update_forecast_max(WEATHER)
        cancelled_calls = len(hass.calls)

        state["cancel"] = False
        await entry._async_update_forecast_max(WEATHER)
        return hass, entry, cancelled_calls

    hass, entry, cancelled_calls = _run(scenario())
    assert cancelled_calls == 1
    assert len(hass.calls) == 2, "second refresh served the poisoned entry"
    assert entry._max_forecast_temp == 81.0
    assert _cache(hass).success is True


def test_fetched_at_is_stamped_after_the_call_completes() -> None:
    """The TTL should measure from when the answer was known."""

    async def scenario():
        marks: dict[str, dt.datetime] = {}

        async def _responder(forecast_type: str):
            await asyncio.sleep(0.05)
            marks["returned"] = dt.datetime.now(dt.UTC)
            return await _daily(70.0)(forecast_type)

        hass = _Hass(_responder)
        entry = _Entry(hass)
        await entry._async_update_forecast_max(WEATHER)
        return hass, marks

    hass, marks = _run(scenario())
    assert _cache(hass).fetched_at >= marks["returned"]


def test_twice_daily_selection_uses_home_assistants_date() -> None:
    """The forecast datetimes are local wall-clock, so the local date must match.

    The first entry is yesterday's, so reading the date from the OS instead of
    Home Assistant finds no match and silently falls back to it.
    """

    async def scenario():
        async def _responder(forecast_type: str):
            if forecast_type == "daily":
                return {WEATHER: {"forecast": []}}
            return {
                WEATHER: {
                    "forecast": [
                        {
                            "datetime": "2019-06-14T12:00:00",
                            "is_daytime": True,
                            "temperature": 55.0,
                        },
                        {
                            "datetime": "2019-06-15T12:00:00",
                            "is_daytime": True,
                            "temperature": 70.0,
                        },
                    ]
                }
            }

        hass = _Hass(_responder)
        entry = _Entry(hass)
        await entry._async_update_forecast_max(WEATHER)
        return entry

    entry = _run(scenario())
    assert entry._max_forecast_temp == 70.0, (
        "picked another day's forecast high, so the date came from the OS"
    )
