"""Regression: overcast must gate summer closing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

from custom_components.adaptive_cover.calculation import (
    ClimateCoverData,
    ClimateCoverState,
)


def _climate_with_clouds(clouds: float) -> ClimateCoverData:
    data = ClimateCoverData.__new__(ClimateCoverData)
    data.hass = MagicMock()
    data.logger = MagicMock()
    data.temp_entity = None
    data.temp_low = 21
    data.temp_high = 25
    data.presence_entity = None
    data.weather_entity = "weather.test"
    data.weather_condition = ["sunny", "partlycloudy", "cloudy", "clear"]
    data.outside_entity = None
    data.temp_switch = False
    data.blind_type = "cover_blind"
    data.transparent_blind = False
    data.lux_entity = None
    data.irradiance_entity = None
    data.lux_threshold = None
    data.irradiance_threshold = None
    data.temp_summer_outside = None
    data._use_lux = False
    data._use_irradiance = False
    data.cloud_coverage_entity = "sensor.clouds"
    data.hass.states.get.side_effect = lambda eid: (
        SimpleNamespace(state=str(clouds))
        if eid == "sensor.clouds"
        else SimpleNamespace(state="cloudy", attributes={})
    )
    return data


def _cover(default: int = 60) -> SimpleNamespace:
    return SimpleNamespace(
        valid=True,
        default=default,
        apply_max_position=False,
        apply_min_position=False,
        max_pos=100,
        min_pos=0,
        logger=MagicMock(),
        mode="mode1",
    )


def test_summer_with_presence_cloud90_uses_default_not_geometry() -> None:
    """Summer + occupants + 90% cloud must use default, not anti-glare geometry."""
    climate = _climate_with_clouds(90.0)
    with (
        patch.object(
            ClimateCoverData, "is_summer", new_callable=PropertyMock, return_value=True
        ),
        patch.object(
            ClimateCoverData, "is_winter", new_callable=PropertyMock, return_value=False
        ),
        patch(
            "custom_components.adaptive_cover.calculation.NormalCoverState.get_state",
            return_value=15,
        ),
    ):
        assert climate.is_sunny is False
        assert ClimateCoverState(_cover(), climate).normal_with_presence() == 60


def test_summer_without_presence_cloud90_uses_default_not_zero() -> None:
    """Summer + no occupants + 90% cloud must use default, not force-close."""
    climate = _climate_with_clouds(90.0)
    with patch.object(
        ClimateCoverData, "is_summer", new_callable=PropertyMock, return_value=True
    ):
        assert ClimateCoverState(_cover(), climate).normal_without_presence() == 60


def test_tilt_summer_with_presence_cloud90_uses_default() -> None:
    """Tilt summer + occupants + overcast uses default, not 45° partial close."""
    climate = _climate_with_clouds(90.0)
    climate.blind_type = "cover_tilt"
    with (
        patch.object(
            ClimateCoverData, "is_summer", new_callable=PropertyMock, return_value=True
        ),
        patch.object(
            ClimateCoverData, "is_winter", new_callable=PropertyMock, return_value=False
        ),
    ):
        assert ClimateCoverState(_cover(), climate).tilt_with_presence(90) == 60


def test_tilt_summer_without_presence_cloud90_uses_default_not_zero() -> None:
    """Tilt summer + no occupants + overcast uses default, not force-close."""
    climate = _climate_with_clouds(90.0)
    climate.blind_type = "cover_tilt"
    cover = _cover()
    cover.beta = 0.0
    with patch.object(
        ClimateCoverData, "is_summer", new_callable=PropertyMock, return_value=True
    ):
        assert ClimateCoverState(cover, climate).tilt_without_presence(90) == 60
