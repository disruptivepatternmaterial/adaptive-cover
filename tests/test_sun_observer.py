"""Regression: the astral migration off the deprecated get_astral_location."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover import sun as sun_mod

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


def test_utc_conversion_matches_the_old_location_wrapper() -> None:
    """astral.Location converted to UTC internally; skipping that drifts.

    astral.sun.zenith_and_azimuth() derives the Julian day from the datetime's
    own date but the time of day from its UTC hour, so a Pacific evening
    timestamp gets today's date paired with tomorrow's hour.
    """
    pytest.importorskip("pytz")
    astral = pytest.importorskip("astral")
    from astral import LocationInfo, sun as astral_sun
    from astral.location import Location

    observer = astral.Observer(LAT, LON, ELEV)
    location = Location(LocationInfo("", "", TZ, LAT, LON))
    zone = dt.timezone(dt.timedelta(hours=-7))
    moment = dt.datetime(2026, 8, 27, 20, 0, tzinfo=zone)

    converted = sun_mod._utc(moment)
    assert converted.utcoffset() == dt.timedelta(0)

    for astral_call, location_call in (
        (astral_sun.elevation, location.solar_elevation),
        (astral_sun.azimuth, location.solar_azimuth),
    ):
        assert astral_call(observer, converted) == pytest.approx(
            location_call(moment, ELEV), abs=1e-9
        )
        # The trap: the same call without the conversion does not agree.
        assert astral_call(observer, moment) != pytest.approx(
            location_call(moment, ELEV), abs=1e-6
        )


def test_solar_helpers_accept_a_caller_supplied_index() -> None:
    """calculation.solar_times() snapshots `times` once and passes it in."""
    pytest.importorskip("astral")
    data = sun_mod.SunData("UTC", MagicMock())
    moments = [dt.datetime(2026, 8, 27, hour, 0, tzinfo=dt.UTC) for hour in (6, 12, 18)]
    azimuths = data.solar_azimuth_for(moments)
    elevations = data.solar_elevation_for(moments)
    assert len(azimuths) == len(elevations) == 3
    assert all(isinstance(value, float) for value in azimuths + elevations)
    # Noon must be the highest sun of the three.
    assert elevations[1] == max(elevations)
