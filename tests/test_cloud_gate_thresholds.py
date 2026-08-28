"""Regression: a numeric cloud reading must not be overridden or discarded.

is_sunny() gates the whole climate path: when it is False the cover falls back
to its default position instead of anti-glare geometry. Two ways the old logic
got that wrong:

* Any cloud reading above 65% deferred to the weather allow-list whenever the
  condition string was not overcast-like. A user whose allow-list contained
  `partlycloudy` therefore read as sunny under a 100% overcast sky.
* A cloud reading was discarded entirely when no allow-list was configured,
  so a dedicated cloud sensor reported sunny at every coverage.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover.calculation import (
    CLOUD_OVERCAST_COVERAGE,
    CLOUD_SUNNY_COVERAGE,
    CLOUD_VETO_COVERAGE,
    ClimateCoverData,
)

ALLOW_LIST = ["sunny", "partlycloudy", "clear"]


def _climate(
    *,
    clouds: str | float | None,
    weather: str | None = "partlycloudy",
    allow_list: list[str] | None = None,
) -> ClimateCoverData:
    """Build a ClimateCoverData exposing only what is_sunny() reads."""
    data = ClimateCoverData.__new__(ClimateCoverData)
    data.hass = MagicMock()
    data.logger = MagicMock()
    data.weather_entity = "weather.test" if weather is not None else None
    data.weather_condition = allow_list
    data.cloud_coverage_entity = "sensor.clouds" if clouds is not None else None

    def _get(entity_id):  # noqa: ANN001, ANN202
        if entity_id == "sensor.clouds":
            return SimpleNamespace(state=str(clouds), attributes={})
        if entity_id == "weather.test":
            return SimpleNamespace(state=weather, attributes={})
        return None

    data.hass.states.get.side_effect = _get
    return data


def test_thresholds_are_ordered() -> None:
    """The bands only make sense in this order."""
    assert CLOUD_SUNNY_COVERAGE < CLOUD_OVERCAST_COVERAGE < CLOUD_VETO_COVERAGE


@pytest.mark.parametrize("clouds", [90.0, 95.0, 100.0])
def test_heavy_cloud_vetoes_an_allow_listed_weather_string(clouds: float) -> None:
    """This is the bug: `partlycloudy` used to win against a 100% reading."""
    climate = _climate(clouds=clouds, weather="partlycloudy", allow_list=ALLOW_LIST)
    assert climate.is_sunny is False


@pytest.mark.parametrize("clouds", [66.0, 75.0, 89.9])
def test_broken_deck_still_defers_to_the_weather_allow_list(clouds: float) -> None:
    """Between 65 and 90 the deliberate broken-deck behaviour is preserved."""
    climate = _climate(clouds=clouds, weather="partlycloudy", allow_list=ALLOW_LIST)
    assert climate.is_sunny is True


@pytest.mark.parametrize("clouds", [66.0, 99.0])
def test_high_cloud_with_overcast_condition_is_not_sunny(clouds: float) -> None:
    """An overcast condition string agrees with the reading."""
    climate = _climate(clouds=clouds, weather="rainy", allow_list=ALLOW_LIST)
    assert climate.is_sunny is False


@pytest.mark.parametrize("clouds", [0.0, 34.9])
def test_low_cloud_is_sunny_even_off_the_allow_list(clouds: float) -> None:
    """Below 35% the reading decides on its own."""
    climate = _climate(clouds=clouds, weather="exceptional", allow_list=ALLOW_LIST)
    assert climate.is_sunny is True


def test_deadband_defers_to_the_allow_list() -> None:
    """35-65 is inconclusive, so the weather string decides."""
    on_list = _climate(clouds=50.0, weather="partlycloudy", allow_list=ALLOW_LIST)
    off_list = _climate(clouds=50.0, weather="cloudy", allow_list=ALLOW_LIST)
    assert on_list.is_sunny is True
    assert off_list.is_sunny is False


@pytest.mark.parametrize(
    ("clouds", "expected"),
    [(0.0, True), (35.0, True), (65.0, True), (65.1, False), (100.0, False)],
)
def test_cloud_reading_decides_when_no_allow_list_is_configured(
    clouds: float, expected: bool
) -> None:
    """With no allow-list the reading used to be discarded and read as sunny."""
    climate = _climate(clouds=clouds, weather=None, allow_list=None)
    assert climate.is_sunny is expected


def test_high_cloud_with_a_weather_entity_but_no_allow_list_is_not_sunny() -> None:
    """The case the discarded reading actually changed.

    A weather entity reporting a non-overcast condition, a cloud sensor at 75%,
    and no allow-list configured: the old code reached `return True` and called
    it sunny. The reading is the only evidence available, so it decides.
    """
    climate = _climate(clouds=75.0, weather="partlycloudy", allow_list=None)
    assert climate.is_sunny is False


def test_non_numeric_cloud_reading_falls_back_to_the_allow_list() -> None:
    """An unavailable or malformed sensor must not decide anything."""
    climate = _climate(clouds="not-a-number", weather="cloudy", allow_list=ALLOW_LIST)
    assert climate.is_sunny is False
    assert any(
        "not numeric" in str(call) for call in climate.logger.debug.call_args_list
    ), "a malformed reading should say so in the log, not vanish"


def test_no_source_at_all_is_sunny() -> None:
    """With nothing configured the climate path stays enabled."""
    climate = _climate(clouds=None, weather=None, allow_list=None)
    assert climate.is_sunny is True
