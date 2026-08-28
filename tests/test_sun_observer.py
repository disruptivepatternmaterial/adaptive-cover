"""Regression: the astral migration off the deprecated get_astral_location."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover import sun as sun_mod
from tests.harness import requires_real

LAT = 48.7766295
LON = -122.428824
ELEV = 137
TZ = "America/Los_Angeles"

SOURCE = Path(sun_mod.__file__).read_text(encoding="utf-8")


def test_source_does_not_use_the_deprecated_helper() -> None:
    """get_astral_location is removed in HA Core 2027.7 and warns until then."""
    assert "get_astral_location" not in SOURCE
    assert "get_astral_observer" in SOURCE


def test_sundata_holds_an_observer_not_a_location() -> None:
    """Nothing may keep an astral Location around; it needs the deprecated call."""
    data = sun_mod.SunData("UTC", MagicMock())
    assert hasattr(data, "observer")
    assert not hasattr(data, "location")


def test_utc_conversion_preserves_the_instant() -> None:
    """_utc() must re-express a moment in UTC, not relabel or shift it."""
    zone = dt.timezone(dt.timedelta(hours=-7))
    moment = dt.datetime(2026, 8, 27, 20, 0, tzinfo=zone)

    converted = sun_mod._utc(moment)

    assert converted.utcoffset() == dt.timedelta(0)
    assert converted == moment
    assert converted.hour == 3 and converted.day == 28


def test_solar_position_depends_on_the_instant_not_the_written_offset() -> None:
    """The same moment in two timezones must give the same sun position.

    This is the defect _utc() exists to prevent. astral 2.2's
    zenith_and_azimuth() takes the Julian day from the datetime's own date
    fields but the time of day from its UTC hour, so a Pacific evening
    timestamp gets today's date paired with tomorrow's hour -- measured at 0.288
    degrees of elevation error for this moment. astral fixed that after 2.2, so
    asserting the *presence* of the drift (as this test used to) turns CI red on
    a dependency upgrade. Asserting that our own code path is instant-based
    holds on every version, and still fails on the pinned astral 2.2 if the
    conversion is removed.
    """
    requires_real("astral")

    pacific = dt.datetime(
        2026, 8, 27, 20, 0, tzinfo=dt.timezone(dt.timedelta(hours=-7))
    )
    same_moment_utc = pacific.astimezone(dt.UTC)
    assert pacific.date() != same_moment_utc.date(), "need a day-straddling moment"

    data = sun_mod.SunData(TZ, MagicMock())

    assert data.solar_elevation_for([pacific]) == pytest.approx(
        data.solar_elevation_for([same_moment_utc]), abs=1e-9
    )
    assert data.solar_azimuth_for([pacific]) == pytest.approx(
        data.solar_azimuth_for([same_moment_utc]), abs=1e-9
    )


def test_solar_position_agrees_with_a_utc_explicit_astral_call() -> None:
    """Pin the absolute value, so instant-invariance cannot be met by a constant."""
    requires_real("astral")
    from astral import Observer, sun as astral_sun

    pacific = dt.datetime(
        2026, 8, 27, 20, 0, tzinfo=dt.timezone(dt.timedelta(hours=-7))
    )
    observer = Observer(LAT, LON, ELEV)
    data = sun_mod.SunData(TZ, MagicMock())
    data.observer = observer

    expected = astral_sun.elevation(observer, pacific.astimezone(dt.UTC))
    assert data.solar_elevation_for([pacific])[0] == pytest.approx(expected, abs=1e-9)


def test_solar_helpers_accept_a_caller_supplied_index() -> None:
    """calculation.solar_times() snapshots `times` once and passes it in."""
    requires_real("astral")
    data = sun_mod.SunData("UTC", MagicMock())
    moments = [dt.datetime(2026, 8, 27, hour, 0, tzinfo=dt.UTC) for hour in (6, 12, 18)]
    azimuths = data.solar_azimuth_for(moments)
    elevations = data.solar_elevation_for(moments)
    assert len(azimuths) == len(elevations) == 3
    assert all(isinstance(value, float) for value in azimuths + elevations)
    # Noon must be the highest sun of the three.
    assert elevations[1] == max(elevations)
