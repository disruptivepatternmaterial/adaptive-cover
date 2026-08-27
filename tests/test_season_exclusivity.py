"""Regression: is_summer and is_winter must never both be true.

is_summer can be driven by predictive_heat, which is a statement about later
today, while is_winter is a statement about now. Before the fix, any current
temperature between temp_summer_outside and temp_low satisfied both on a day
forecast to be hot, and the coordinator logged a WARNING blaming the
temp_high/temp_low thresholds -- which were not the cause.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.adaptive_cover.calculation import ClimateCoverData

# Shapes taken from a real ten-entry install (degrees Fahrenheit).
CONFIGS = [
    (66.0, 72.0, 65.0),
    (68.0, 72.0, 65.0),
    (66.0, 75.0, 65.0),
    (70.0, 84.0, 70.0),
]


def _climate(
    temp_low: float,
    temp_high: float,
    outside_threshold: float,
    outside: float,
    inside: float,
    forecast: float | None,
) -> ClimateCoverData:
    """Build a ClimateCoverData the way the coordinator does.

    temp_switch is True, matching an install that uses outdoor temperature as
    the current temperature, which is what exposed the overlap.
    """
    data = ClimateCoverData.__new__(ClimateCoverData)
    data.hass = MagicMock()
    data.logger = MagicMock()
    data.temp_entity = "sensor.inside"
    data.temp_low = temp_low
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
    data.temp_summer_outside = outside_threshold
    data._use_lux = False
    data._use_irradiance = False
    data.cloud_coverage_entity = None
    data.max_forecast_temp = forecast
    data.hass.states.get.side_effect = lambda eid: SimpleNamespace(
        state=str(outside if eid == "sensor.outside" else inside), attributes={}
    )
    return data


def test_seasons_are_mutually_exclusive_across_the_domain() -> None:
    """No (config, outside, forecast, inside) combination may report both."""
    both_true = []
    for temp_low, temp_high, threshold in CONFIGS:
        for step in range(141):
            outside = 55.0 + 0.25 * step
            for forecast in (None, 40.0, 66.0, 67.0, 68.0, 80.0, 100.0):
                for inside in (50.0, 70.0, 95.0):
                    climate = _climate(
                        temp_low, temp_high, threshold, outside, inside, forecast
                    )
                    if climate.is_summer and climate.is_winter:
                        both_true.append((temp_low, outside, forecast, inside))
    assert not both_true, f"{len(both_true)} overlapping cases, e.g. {both_true[:3]}"


def test_the_overlap_band_now_reports_summer_only() -> None:
    """Cool now, hot later: heat rejection wins, because that is the point.

    outside 65.5 F sits between the 65 F outdoor threshold and the 66 F
    temp_low, and the forecast high of 80 F is above threshold + 2.
    """
    climate = _climate(66.0, 72.0, 65.0, 65.5, 70.0, 80.0)
    assert climate.is_summer is True
    assert climate.is_winter is False


def test_genuine_cold_still_reports_winter() -> None:
    """The exclusivity rule must not swallow real heating demand."""
    for temp_low, temp_high, threshold in CONFIGS:
        climate = _climate(temp_low, temp_high, threshold, 35.0, 40.0, 38.0)
        assert climate.is_winter is True
        assert climate.is_summer is False


def test_genuine_heat_still_reports_summer() -> None:
    """And real cooling demand is untouched."""
    for temp_low, temp_high, threshold in CONFIGS:
        climate = _climate(temp_low, temp_high, threshold, 95.0, 95.0, 99.0)
        assert climate.is_summer is True
        assert climate.is_winter is False


def test_no_forecast_falls_back_to_the_current_temperature_only() -> None:
    """predictive_heat must not fire when the forecast is unavailable."""
    climate = _climate(66.0, 72.0, 65.0, 65.5, 70.0, None)
    assert climate.is_summer is False
    assert climate.is_winter is True
