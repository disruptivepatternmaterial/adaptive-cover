"""Regression: every input that steers a cover must also schedule the update.

`sensor.outside_average_temperature` decides `is_summer` for all ten entries on
the production install, and with the Outside Temperature switch on it *is* the
current temperature -- but it was absent from the state-change listener. The
value was never stale, because it is re-read on each update; what was missing
was the trigger. Covers reacted to outdoor temperature only when the indoor
sensor happened to publish, and an entry configured with an outdoor sensor and
no indoor one would not react to it at all.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.adaptive_cover import tracked_entities
from custom_components.adaptive_cover.const import (
    CONF_CLOUD_COVERAGE_ENTITY,
    CONF_END_ENTITY,
    CONF_OUTSIDETEMP_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_START_ENTITY,
    CONF_TEMP_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_ENTITY,
)


def _entry(**options) -> SimpleNamespace:
    return SimpleNamespace(options=options)


def test_outside_temperature_entity_is_tracked() -> None:
    """The defect: the outdoor sensor drove decisions but triggered no update."""
    entities = tracked_entities(
        _entry(**{CONF_OUTSIDETEMP_ENTITY: "sensor.outside_average_temperature"})
    )
    assert "sensor.outside_average_temperature" in entities


def test_every_configured_input_is_tracked() -> None:
    """A full climate-mode entry, shaped like the production config."""
    entities = tracked_entities(
        _entry(
            **{
                CONF_TEMP_ENTITY: "sensor.living_room_average_temperature",
                CONF_OUTSIDETEMP_ENTITY: "sensor.outside_average_temperature",
                CONF_PRESENCE_ENTITY: "binary_sensor.presence",
                CONF_WEATHER_ENTITY: "weather.forecast_home",
                CONF_CLOUD_COVERAGE_ENTITY: "sensor.openweathermap_cloud_coverage",
                CONF_START_ENTITY: "input_datetime.shades_open_morning",
                CONF_END_ENTITY: "input_datetime.shades_close_night",
                CONF_WINDOW_ENTITY: ["binary_sensor.door_contact"],
            }
        )
    )
    assert set(entities) == {
        "sun.sun",
        "sensor.living_room_average_temperature",
        "sensor.outside_average_temperature",
        "binary_sensor.presence",
        "weather.forecast_home",
        "sensor.openweathermap_cloud_coverage",
        "input_datetime.shades_open_morning",
        "input_datetime.shades_close_night",
        "binary_sensor.door_contact",
    }


def test_sun_is_always_tracked_even_with_nothing_configured() -> None:
    """Solar geometry drives every cover, configured inputs or not."""
    assert tracked_entities(_entry()) == ["sun.sun"]


def test_a_legacy_string_window_entity_is_still_tracked() -> None:
    """window_entity predates the multi-select and may be a bare string."""
    entities = tracked_entities(_entry(**{CONF_WINDOW_ENTITY: "binary_sensor.door"}))
    assert "binary_sensor.door" in entities


def test_an_entity_used_twice_is_registered_once() -> None:
    """Duplicates would append two callbacks and refresh twice per change."""
    shared = "sensor.one_thermometer"
    entities = tracked_entities(
        _entry(**{CONF_TEMP_ENTITY: shared, CONF_OUTSIDETEMP_ENTITY: shared})
    )
    assert entities.count(shared) == 1
