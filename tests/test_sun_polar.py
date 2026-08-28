"""Regression: polar day and polar night must not fail the coordinator.

astral solves for a horizon crossing with math.acos. Inside the polar circles
there are days with no crossing at all, so the argument leaves [-1, 1] and
astral lets the domain error surface as a bare ValueError:

    ValueError: expected a number in range from -1 up to 1, got -1.15...

Unguarded, that propagates out of sunset_valid, through _async_refresh_data,
and is rewrapped as UpdateFailed, so every entity on the entry goes unavailable
for the weeks the condition lasts.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover import sun as sun_mod
from custom_components.adaptive_cover.calculation import AdaptiveGeneralCover

# Tromso, Norway: comfortably inside the Arctic Circle.
TROMSO_LAT = 69.6492
TROMSO_LON = 18.9553
POLAR_DAY = dt.date(2026, 6, 21)
POLAR_NIGHT = dt.date(2026, 12, 21)
ORDINARY_DAY = dt.date(2026, 9, 21)


@pytest.fixture
def arctic_sun_data(monkeypatch: pytest.MonkeyPatch):
    """SunData observing Tromso, with the date under the test's control."""
    astral = pytest.importorskip("astral")

    def _build(day: dt.date) -> sun_mod.SunData:
        data = sun_mod.SunData("Europe/Oslo", MagicMock())
        data.observer = astral.Observer(TROMSO_LAT, TROMSO_LON, 0)
        monkeypatch.setattr(data, "today", lambda: day)
        return data

    return _build


def test_astral_still_raises_for_a_polar_day() -> None:
    """Guard the premise: if astral stops raising, these tests prove nothing."""
    astral = pytest.importorskip("astral")
    from astral import sun as astral_sun

    observer = astral.Observer(TROMSO_LAT, TROMSO_LON, 0)
    for event in (astral_sun.sunrise, astral_sun.sunset):
        with pytest.raises(ValueError, match="range from -1"):
            event(observer, POLAR_DAY)


@pytest.mark.parametrize("day", [POLAR_DAY, POLAR_NIGHT])
def test_sunrise_and_sunset_return_none_instead_of_raising(
    arctic_sun_data, day: dt.date
) -> None:
    """Both polar cases must degrade to None, not an exception."""
    data = arctic_sun_data(day)
    assert data.sunrise() is None
    assert data.sunset() is None


def test_ordinary_day_still_returns_real_times(arctic_sun_data) -> None:
    """The guard must not swallow the normal case."""
    data = arctic_sun_data(ORDINARY_DAY)
    sunrise = data.sunrise()
    sunset = data.sunset()
    assert sunrise is not None
    assert sunset is not None
    assert sunrise < sunset
    assert sunrise.date() == ORDINARY_DAY


def test_solar_elevation_now_distinguishes_the_two_polar_cases(
    arctic_sun_data,
) -> None:
    """sunset_valid resolves polar days by elevation, so it must be signed right."""
    pytest.importorskip("astral")
    from astral import sun as astral_sun

    data = arctic_sun_data(POLAR_DAY)
    noon_polar_day = astral_sun.elevation(
        data.observer, dt.datetime(2026, 6, 21, 10, 0, tzinfo=dt.UTC)
    )
    noon_polar_night = astral_sun.elevation(
        data.observer, dt.datetime(2026, 12, 21, 10, 0, tzinfo=dt.UTC)
    )
    assert noon_polar_day > 0
    assert noon_polar_night < 0

    # And the live helper agrees with astral for the current moment.
    assert data.solar_elevation_now() == pytest.approx(
        astral_sun.elevation(data.observer, dt.datetime.now(dt.UTC)), abs=0.01
    )


class _Gate:
    """Minimal stand-in exposing only what sunset_valid touches."""

    sunset_valid = AdaptiveGeneralCover.sunset_valid

    def __init__(self, sun_data) -> None:  # noqa: ANN001
        self.sun_data = sun_data
        self.logger = MagicMock()
        self.sunset_off = 0
        self.sunrise_off = 0


def _sun_data(*, elevation: float, sunrise=None, sunset=None) -> SimpleNamespace:  # noqa: ANN001
    return SimpleNamespace(
        sunrise=lambda: sunrise,
        sunset=lambda: sunset,
        solar_elevation_now=lambda: elevation,
    )


def test_polar_day_is_not_treated_as_night() -> None:
    """Sun above the horizon all day: the sunset position must not engage."""
    gate = _Gate(_sun_data(elevation=42.5))
    assert gate.sunset_valid is False


def test_polar_night_is_treated_as_night() -> None:
    """Sun below the horizon all day: the sunset position should hold."""
    gate = _Gate(_sun_data(elevation=-4.05))
    assert gate.sunset_valid is True


def test_ordinary_day_uses_the_sunrise_sunset_comparison() -> None:
    """With real crossings, the offset comparison still decides."""
    now = dt.datetime.now(dt.UTC)
    daytime = _Gate(
        _sun_data(
            elevation=30.0,
            sunrise=now - dt.timedelta(hours=4),
            sunset=now + dt.timedelta(hours=4),
        )
    )
    nighttime = _Gate(
        _sun_data(
            elevation=-10.0,
            sunrise=now - dt.timedelta(hours=12),
            sunset=now - dt.timedelta(hours=2),
        )
    )
    assert daytime.sunset_valid is False
    assert nighttime.sunset_valid is True
