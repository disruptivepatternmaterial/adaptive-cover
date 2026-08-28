"""Regression: "today" must mean today in Home Assistant's timezone.

date.today() and datetime.now(UTC).date() both read a date that has nothing to
do with the timezone the user configured in Home Assistant. A container running
with TZ=UTC while Home Assistant is set to America/Los_Angeles disagrees with
itself for the last 7 hours of every local day, which is exactly the evening
window covers are most active in.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover import coordinator as coordinator_mod
from custom_components.adaptive_cover import sun as sun_mod

Coordinator = coordinator_mod.AdaptiveDataUpdateCoordinator

PACIFIC = dt.timezone(dt.timedelta(hours=-7))
# 21:30 Pacific is already the next day in UTC, so the local date and the UTC
# date disagree at this instant. The date is deliberately fixed in the past and
# clear of a DST transition: a date that could coincide with the machine's own
# today() would let the bug satisfy these assertions by luck.
LOCAL_EVENING = dt.datetime(2019, 6, 15, 21, 30, tzinfo=PACIFIC)
LOCAL_DATE = dt.date(2019, 6, 15)


@pytest.fixture
def ha_local_evening(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin Home Assistant's clock to a local evening that is tomorrow in UTC."""
    assert LOCAL_EVENING.astimezone(dt.UTC).date() != LOCAL_DATE, (
        "fixture must straddle the UTC date boundary to be meaningful"
    )
    monkeypatch.setattr(sun_mod.dt_util, "now", lambda: LOCAL_EVENING)
    monkeypatch.setattr(coordinator_mod.dt_util, "now", lambda: LOCAL_EVENING)


def test_today_follows_home_assistant_not_the_os(ha_local_evening: None) -> None:
    """SunData.today() must return the HA-local date, not the UTC one."""
    data = sun_mod.SunData("America/Los_Angeles", MagicMock())
    assert data.today() == LOCAL_DATE
    assert data.today() != LOCAL_EVENING.astimezone(dt.UTC).date()


def test_solar_grid_starts_on_the_local_date(ha_local_evening: None) -> None:
    """The 5-minute grid must cover the local day, not the UTC one."""
    pytest.importorskip("pandas")
    data = sun_mod.SunData("America/Los_Angeles", MagicMock())
    times = data.times
    assert times[0].date() == LOCAL_DATE
    # A full day at 5-minute spacing, inclusive of both endpoints.
    assert len(times) == 289


def test_sunrise_and_sunset_use_the_local_date(
    ha_local_evening: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sunrise/sunset comparisons drive the sunset_valid gate; wrong day, wrong gate."""
    pytest.importorskip("astral")
    seen: list[dt.date] = []

    def _record(_observer, day):  # noqa: ANN001, ANN202
        seen.append(day)
        return dt.datetime(day.year, day.month, day.day, 12, 0, tzinfo=dt.UTC)

    monkeypatch.setattr(sun_mod.astral_sun, "sunrise", _record)
    monkeypatch.setattr(sun_mod.astral_sun, "sunset", _record)

    data = sun_mod.SunData("America/Los_Angeles", MagicMock())
    data.sunrise()
    data.sunset()

    assert seen == [LOCAL_DATE, LOCAL_DATE]


class _SolarEntry:
    """Minimal stand-in exposing only what the solar-times cache touches."""

    _async_solar_times = Coordinator._async_solar_times

    def __init__(self) -> None:
        self.logger = MagicMock()
        self.first_refresh = False
        self._sun_start_time = None
        self._sun_end_time = None
        self._sun_times_date = None
        self.computations = 0
        self.hass = SimpleNamespace(async_add_executor_job=self._executor_job)

    async def _executor_job(self, func):  # noqa: ANN001, ANN202
        self.computations += 1
        return func()


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


def _cover(start, end):  # noqa: ANN001, ANN202
    return SimpleNamespace(solar_times=lambda: (start, end))


def test_solar_grid_is_computed_once_per_local_day(ha_local_evening: None) -> None:
    """Repeated refreshes inside one local day must reuse the cached window."""
    entry = _SolarEntry()
    window = (
        dt.datetime(2026, 8, 27, 7, 0, tzinfo=PACIFIC),
        dt.datetime(2026, 8, 27, 19, 0, tzinfo=PACIFIC),
    )
    cover = _cover(*window)

    results = [_run(entry._async_solar_times(cover)) for _ in range(5)]

    assert entry.computations == 1
    assert all(result == window for result in results)
    assert entry._sun_times_date == LOCAL_DATE


def test_window_the_sun_never_reaches_is_still_cached(ha_local_evening: None) -> None:
    """solar_times() returning (None, None) must not rebuild the grid every cycle.

    A north-facing window in winter never sees the sun enter its aperture, so
    the old guard, which recomputed whenever _sun_start_time was None, rebuilt
    all 289 samples on every single update.
    """
    entry = _SolarEntry()
    cover = _cover(None, None)

    for _ in range(5):
        assert _run(entry._async_solar_times(cover)) == (None, None)

    assert entry.computations == 1


def test_grid_is_rebuilt_when_the_local_day_rolls_over(
    ha_local_evening: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crossing local midnight must invalidate the cache."""
    entry = _SolarEntry()
    cover = _cover(*(LOCAL_EVENING, LOCAL_EVENING))

    _run(entry._async_solar_times(cover))
    assert entry.computations == 1

    tomorrow = LOCAL_EVENING + dt.timedelta(days=1)
    monkeypatch.setattr(coordinator_mod.dt_util, "now", lambda: tomorrow)
    _run(entry._async_solar_times(cover))

    assert entry.computations == 2
    assert entry._sun_times_date == tomorrow.date()


def test_utc_date_rollover_alone_does_not_rebuild_the_grid(
    ha_local_evening: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old guard rebuilt at UTC midnight, mid-afternoon for Pacific users."""
    entry = _SolarEntry()
    cover = _cover(*(LOCAL_EVENING, LOCAL_EVENING))

    _run(entry._async_solar_times(cover))
    assert entry.computations == 1

    # 23:30 local: the UTC date has advanced twice over since the first call,
    # but it is still the same local day.
    later_same_local_day = LOCAL_EVENING + dt.timedelta(hours=2)
    assert later_same_local_day.date() == LOCAL_DATE
    monkeypatch.setattr(coordinator_mod.dt_util, "now", lambda: later_same_local_day)
    _run(entry._async_solar_times(cover))

    assert entry.computations == 1
