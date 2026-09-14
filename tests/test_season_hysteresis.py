"""Regression: seasonal thresholds must not chatter, and a stale forecast must not close covers.

Both defects were observed together on a ten-entry production install on
2026-09-13 and are reproduced here from that day's recorder output.

Summer and winter pick opposite ends of the cover's travel -- winter with the
sun in the window returns 100, summer runs anti-glare geometry that bottomed
out at 1-5% on west windows at a low evening sun -- so every season flip is a
full-travel move on every motor in the entry. `sensor.outside_average_temperature`
dithered across a 65 degree threshold (64.94 -> 65.62 -> 65.12) and the house
cycled open and shut twice inside seven minutes, 6-21 times a day for a week.

Holding it there was `predictive_heat`: today's forecast high of ~72 exceeded
the 65 degree outdoor threshold, which kept summer armed at 18:37 with the sun
at ~9 degrees of elevation and the outdoor temperature falling through the
threshold. The README documents that check as engaging summer "before the room
heats up"; by sunset the heat is behind us, not ahead.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.adaptive_cover.calculation import (
    PREDICTIVE_HEAT_MIN_ELEVATION,
    SEASON_TEMP_HYSTERESIS,
    ClimateCoverData,
)

# Main Room Window Shades, verbatim from the production config entry.
OUTSIDE_THRESHOLD = 65.0
TEMP_LOW = 66.0
TEMP_HIGH = 72.0
FORECAST_HIGH = 72.0

# sensor.outside_average_temperature, 12:00-18:40 America/Los_Angeles on
# 2026-09-13, exactly as the recorder stored it.
RECORDED_OUTSIDE_SERIES = [
    64.3, 65.7, 65.58, 65.81, 65.76, 65.71, 65.63, 66.9, 66.61, 66.09,
    65.81, 65.66, 65.47, 66.89, 66.67, 66.5, 66.48, 66.24, 66.03, 67.17,
    66.87, 66.66, 66.23, 65.9, 65.68, 66.97, 66.6, 66.37, 66.22, 66.08,
    66.0, 67.14, 66.9, 66.68, 66.45, 66.36, 66.25, 67.58, 67.37, 67.07,
    66.97, 66.91, 66.78, 68.06, 68.0, 68.13, 68.07, 67.93, 67.81, 69.14,
    68.98, 68.94, 69.11, 69.16, 69.32, 70.57, 70.46, 70.47, 70.33, 70.4,
    70.45, 71.78, 71.65, 71.56, 71.3, 70.95, 70.41, 69.82, 70.55, 69.68,
    68.87, 68.21, 67.67, 67.19, 68.23, 67.6, 67.6, 66.43, 65.35, 64.94,
    65.62, 65.12,
]  # fmt: skip

# The tail that produced the reported symptom: two full-travel cycles across
# every shade in the house between 18:25 and 18:40.
EVENING_TAIL = [65.35, 64.94, 65.62, 65.12]


def _climate(
    outside: float,
    *,
    previous_season: str | None,
    sun_elevation: float | None,
    inside: float = 75.0,
    forecast: float | None = FORECAST_HIGH,
    temp_high: float = TEMP_HIGH,
) -> ClimateCoverData:
    """Build ClimateCoverData the way the coordinator does for this install.

    temp_switch is True: the Outside Temperature switch is on for every entry
    on the affected install, so the outdoor reading *is* the current
    temperature.
    """
    data = ClimateCoverData.__new__(ClimateCoverData)
    data.hass = MagicMock()
    data.logger = MagicMock()
    data.temp_entity = "sensor.inside"
    data.temp_low = TEMP_LOW
    data.temp_high = temp_high
    data.presence_entity = None
    data.weather_entity = None
    data.weather_condition = None
    data.outside_entity = "sensor.outside"
    data.temp_switch = True
    data.blind_type = "cover_blind"
    data.transparent_blind = False
    data.lux_entity = None
    data.irradiance_entity = None
    data.lux_threshold = None
    data.irradiance_threshold = None
    data.temp_summer_outside = OUTSIDE_THRESHOLD
    data._use_lux = False
    data._use_irradiance = False
    data.cloud_coverage_entity = None
    data.previous_season = previous_season
    data.sun_elevation = sun_elevation
    data.max_forecast_temp = forecast
    data.hass.states.get.side_effect = lambda eid: SimpleNamespace(
        state=str(outside if eid == "sensor.outside" else inside), attributes={}
    )
    return data


def _season(climate: ClimateCoverData) -> str:
    """Collapse to the control_method string the coordinator publishes."""
    if climate.is_summer:
        return "summer"
    if climate.is_winter:
        return "winter"
    return "intermediate"


def _replay(series, *, sun_elevation, feed_previous: bool) -> list[str]:
    """Run the series through the season decision.

    feed_previous=False reproduces the pre-fix behaviour, where every update
    re-decided from scratch with no memory of the season already in force.
    """
    seasons: list[str] = []
    previous: str | None = None
    for outside in series:
        season = _season(
            _climate(
                outside,
                previous_season=previous if feed_previous else None,
                sun_elevation=sun_elevation,
            )
        )
        seasons.append(season)
        previous = season
    return seasons


def _transitions(seasons: list[str]) -> int:
    return sum(1 for a, b in zip(seasons, seasons[1:]) if a != b)


def test_recorded_day_stops_chattering() -> None:
    """The recorded series must settle instead of flipping with sensor noise."""
    # Sun high enough that predictive heat is not the thing being tested.
    without = _replay(RECORDED_OUTSIDE_SERIES, sun_elevation=40.0, feed_previous=False)
    with_hysteresis = _replay(
        RECORDED_OUTSIDE_SERIES, sun_elevation=40.0, feed_previous=True
    )

    assert _transitions(without) > _transitions(with_hysteresis)
    # One transition: intermediate/winter at the 64.3 start, then summer for
    # the rest of the afternoon. Anything more is the sensor, not the weather.
    assert _transitions(with_hysteresis) <= 1


def test_evening_dither_does_not_flip_the_season() -> None:
    """65.35 -> 64.94 -> 65.62 -> 65.12 must hold summer, not cycle the motors."""
    assert (
        _transitions(_replay(EVENING_TAIL, sun_elevation=40.0, feed_previous=True)) == 0
    )
    # And the pre-fix path really did flip, so the test above is not vacuous.
    assert (
        _transitions(_replay(EVENING_TAIL, sun_elevation=40.0, feed_previous=False)) > 0
    )


def test_summer_entry_threshold_is_unchanged() -> None:
    """Hysteresis must make leaving sticky without moving the documented edge."""
    just_over = _climate(
        OUTSIDE_THRESHOLD + 0.01, previous_season=None, sun_elevation=40.0
    )
    assert just_over.is_summer is True

    just_under = _climate(
        OUTSIDE_THRESHOLD - 0.01, previous_season=None, sun_elevation=40.0
    )
    assert just_under.is_summer is False


def test_summer_releases_once_the_reading_clears_the_band() -> None:
    """Sticky is not stuck: a real drop still ends summer."""
    inside_band = _climate(
        OUTSIDE_THRESHOLD - SEASON_TEMP_HYSTERESIS + 0.1,
        previous_season="summer",
        sun_elevation=40.0,
    )
    assert inside_band.is_summer is True

    below_band = _climate(
        OUTSIDE_THRESHOLD - SEASON_TEMP_HYSTERESIS - 0.1,
        previous_season="summer",
        sun_elevation=40.0,
    )
    assert below_band.is_summer is False


def test_winter_is_sticky_on_exit_too() -> None:
    """The cold edge gets the same deadband, in the opposite direction."""
    held = _climate(
        TEMP_LOW + SEASON_TEMP_HYSTERESIS - 0.1,
        previous_season="winter",
        sun_elevation=40.0,
        forecast=None,
    )
    assert held.is_winter is True

    released = _climate(
        TEMP_LOW + SEASON_TEMP_HYSTERESIS + 0.1,
        previous_season="winter",
        sun_elevation=40.0,
        forecast=None,
    )
    assert released.is_winter is False


def test_forecast_high_stops_arguing_for_summer_at_a_low_sun() -> None:
    """The reported symptom: 18:37, sun at ~9 degrees, 65.6 F, shades shut."""
    evening = _climate(65.62, previous_season="summer", sun_elevation=9.0)
    assert evening.is_summer is False
    # And with summer gone the cover gets winter's fully-open target instead
    # of anti-glare geometry.
    assert evening.is_winter is True


def test_forecast_high_still_pre_cools_while_the_heat_is_ahead() -> None:
    """A cool morning on a day forecast to be hot must still engage summer."""
    morning = _climate(
        65.62, previous_season=None, sun_elevation=PREDICTIVE_HEAT_MIN_ELEVATION + 1
    )
    assert morning.is_summer is True


def test_measured_heat_ignores_the_elevation_gate() -> None:
    """Only the forecast shortcut is scoped; a genuinely hot room always wins."""
    hot_at_dusk = _climate(
        TEMP_HIGH + 5, previous_season=None, sun_elevation=0.0, forecast=None
    )
    assert hot_at_dusk.is_summer is True


def test_absent_elevation_leaves_predictive_heat_alone() -> None:
    """No sun reading is not evidence of a low sun; do not infer one."""
    unknown_sun = _climate(65.62, previous_season=None, sun_elevation=None)
    assert unknown_sun.is_summer is True
