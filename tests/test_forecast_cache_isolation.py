"""Regression: one entry's bad luck must not degrade the whole installation.

The forecast cache is keyed by weather entity and shared by every config entry
pointing at it, which is what makes it worth having and also what makes its
failure modes house-wide. Three of them:

* the cache lived in ``hass.data[DOMAIN]`` alongside ``entry_id -> coordinator``;
* the value was blanked before each attempt, so one failed fetch served ``None``
  to every sharing entry for the whole failure TTL;
* a wedged weather integration held the shared lock with no bound.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover import (
    async_unload_entry,
    coordinator as coordinator_mod,
)
from custom_components.adaptive_cover.const import CONF_WEATHER_ENTITY, DOMAIN

WEATHER = "weather.test"
Coordinator = coordinator_mod.AdaptiveDataUpdateCoordinator
PACIFIC = dt.timezone(dt.timedelta(hours=-7))
LOCAL_EVENING = dt.datetime(2019, 6, 15, 21, 30, tzinfo=PACIFIC)


@pytest.fixture(autouse=True)
def ha_local_evening(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin Home Assistant's clock so twice_daily selection is deterministic."""
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
    """Run a coroutine on its own loop, leaving the ambient one alone."""
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


# --- the shared namespace must not collide with entry IDs -------------------


def test_the_cache_does_not_sit_beside_the_entry_coordinators() -> None:
    """hass.data[DOMAIN] maps entry_id -> coordinator and nothing else."""
    hass = _Hass(_daily(25.0))
    entry = _Entry(hass)

    _run(entry._async_update_forecast_max(WEATHER))

    domain_data = hass.data[DOMAIN]
    assert coordinator_mod._FORECAST_CACHE_KEY not in domain_data
    assert list(domain_data) == [coordinator_mod.SHARED_DATA_KEY]


# --- a failed fetch must not blank a good value -----------------------------


def test_a_failed_fetch_keeps_the_last_known_good_value() -> None:
    """Pre-clearing served None to every sharing entry for the failure TTL."""
    responses = [_daily(25.0), _fails()]
    hass = _Hass(lambda kind: responses[0](kind))
    entry = _Entry(hass)

    _run(entry._async_update_forecast_max(WEATHER))
    assert entry._max_forecast_temp == 25.0

    # Age the cache past the success TTL so the next call really refetches.
    cached = _cache(hass)
    cached.fetched_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    responses[0] = _fails()

    _run(entry._async_update_forecast_max(WEATHER))

    assert entry._max_forecast_temp == 25.0, (
        "a transient weather failure must not disable predictive_heat "
        "house-wide; the previous high is still the best answer available"
    )


def test_a_failed_fetch_shortens_the_retry_interval() -> None:
    """Keeping the value must not also grant it the full 15-minute TTL."""
    responses = [_daily(25.0)]
    hass = _Hass(lambda kind: responses[0](kind))
    entry = _Entry(hass)

    _run(entry._async_update_forecast_max(WEATHER))
    assert _cache(hass).success is True

    cached = _cache(hass)
    cached.fetched_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    responses[0] = _fails()

    _run(entry._async_update_forecast_max(WEATHER))

    assert _cache(hass).success is False
    assert _cache(hass).value == 25.0


# --- a wedged weather integration must not hold the lock --------------------


def test_a_hung_fetch_is_bounded_and_does_not_fail_the_update() -> None:
    """TimeoutError subclasses Exception, so escaping would mean UpdateFailed.

    _async_update_forecast_max is awaited unguarded inside _async_refresh_data,
    whose `except Exception` converts anything that escapes into UpdateFailed --
    taking every entity on the entry unavailable because an optional forecast
    was slow. The timeout has to be absorbed here.
    """

    async def _hangs(_forecast_type: str):
        await asyncio.sleep(3600)

    hass = _Hass(_hangs)
    entry = _Entry(hass)
    entry._FORECAST_FETCH_TIMEOUT_S = 0.05

    _run(entry._async_update_forecast_max(WEATHER))

    assert entry._max_forecast_temp is None
    assert _cache(hass).success is False
    assert _cache(hass).fetched_at is not None, "must stamp so the retry TTL applies"


def test_a_hung_fetch_releases_the_lock_for_the_next_entry() -> None:
    """Every other entry on this weather entity queues behind that lock."""
    calls = {"n": 0}

    async def _hangs_once(_forecast_type: str):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(3600)
        return {
            WEATHER: {
                "forecast": [{"datetime": "2019-06-15T00:00:00", "temperature": 30.0}]
            }
        }

    hass = _Hass(_hangs_once)
    first = _Entry(hass)
    first._FORECAST_FETCH_TIMEOUT_S = 0.05
    second = _Entry(hass)
    second._FORECAST_FETCH_TIMEOUT_S = 0.05

    async def _both():
        await first._async_update_forecast_max(WEATHER)
        # Age past the failure TTL so the second entry genuinely retries.
        _cache(hass).fetched_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
        await asyncio.wait_for(second._async_update_forecast_max(WEATHER), timeout=5)

    _run(_both())

    assert second._max_forecast_temp == 30.0


def _fails():
    async def _responder(_forecast_type: str):
        raise RuntimeError("weather integration is unavailable")

    return _responder


# --- the cache must be released when nothing needs it any more --------------


def _unload_hass(remaining_entries):
    """Fake hass for async_unload_entry, with platforms that unload cleanly."""

    async def _unload_platforms(_entry, _platforms):
        return True

    return SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": MagicMock(),
                coordinator_mod.SHARED_DATA_KEY: {
                    coordinator_mod._FORECAST_CACHE_KEY: {
                        WEATHER: coordinator_mod._ForecastCache(),
                        "weather.other": coordinator_mod._ForecastCache(),
                    }
                },
            }
        },
        config_entries=SimpleNamespace(
            async_unload_platforms=_unload_platforms,
            async_entries=lambda _domain: remaining_entries,
        ),
    )


def _config_entry(entry_id: str, weather: str | None):
    return SimpleNamespace(
        entry_id=entry_id,
        title=entry_id,
        data={},
        options={CONF_WEATHER_ENTITY: weather} if weather else {},
    )


def test_unloading_the_last_entry_releases_the_shared_cache() -> None:
    """Every _ForecastCache carries an asyncio.Lock and outlived reloads."""
    unloading = _config_entry("entry-1", WEATHER)
    hass = _unload_hass(remaining_entries=[unloading])

    assert _run(async_unload_entry(hass, unloading)) is True

    assert coordinator_mod.SHARED_DATA_KEY not in hass.data[DOMAIN]
    assert hass.data[DOMAIN] == {}


def test_unloading_one_entry_keeps_a_cache_another_still_uses() -> None:
    """Shared means shared: the entry that created it is not its owner."""
    unloading = _config_entry("entry-1", WEATHER)
    survivor = _config_entry("entry-2", WEATHER)
    hass = _unload_hass(remaining_entries=[unloading, survivor])

    assert _run(async_unload_entry(hass, unloading)) is True

    cache = hass.data[DOMAIN][coordinator_mod.SHARED_DATA_KEY][
        coordinator_mod._FORECAST_CACHE_KEY
    ]
    assert WEATHER in cache
    assert "weather.other" not in cache, "no remaining entry watches this one"


def test_unloading_an_entry_that_never_finished_setup_does_not_raise() -> None:
    """An undefended pop turned a failed setup into a masking KeyError."""
    unloading = _config_entry("entry-missing", WEATHER)
    hass = _unload_hass(remaining_entries=[])

    assert _run(async_unload_entry(hass, unloading)) is True
