"""Regression: an untouched outside-temperature threshold must stay unset.

CLIMATE_OPTIONS declared ``vol.Optional(CONF_OUTSIDE_THRESHOLD, default=0)``.
voluptuous materialises a default while validating rather than only offering it
in the form, so the flow persisted a threshold of 0 into every entry whether or
not the user touched the field. Zero gates nothing -- ``outside_high`` degrades
to ``outside > 0`` and ``predictive_heat`` to ``forecast > 2`` -- so summer heat
rejection engaged on a cold spring morning forecast to reach 8 degrees.

Three surfaces have to agree for the fix to hold: the schema must not invent a
value, the migration must remove the values already stored, and the options flow
must let a user clear one.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.adaptive_cover import async_migrate_entry
from custom_components.adaptive_cover.config_flow import (
    CLIMATE_OPTIONS,
    ConfigFlowHandler,
    OptionsFlowHandler,
)
from custom_components.adaptive_cover.const import (
    CONF_OUTSIDE_THRESHOLD,
    CONF_TEMP_ENTITY,
    CONF_TEMP_HIGH,
    CONF_TEMP_LOW,
)

MINIMAL_CLIMATE = {
    CONF_TEMP_ENTITY: "sensor.living_room_temperature",
    CONF_TEMP_LOW: 21,
    CONF_TEMP_HIGH: 25,
}


def _run(coro):
    """Run async test helpers without pytest-asyncio."""
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeEntries:
    """Record what async_update_entry was asked to write."""

    def __init__(self) -> None:
        self.updates: list[dict] = []

    def async_update_entry(self, entry, options=None, **_kwargs) -> None:  # noqa: ANN001
        self.updates.append(options)
        entry.options = options


def _entry(options: dict):
    return SimpleNamespace(
        entry_id="abc123", title="Kitchen Window", options=dict(options)
    )


def _hass():
    return SimpleNamespace(config_entries=_FakeEntries())


def _options_flow(stored: dict) -> OptionsFlowHandler:
    """OptionsFlowHandler over an entry with the given stored options."""
    flow = OptionsFlowHandler(SimpleNamespace(data={}, options=dict(stored)))
    flow._update_options = _create_entry
    return flow


async def _create_entry():
    return {"type": "create_entry"}


# --- the schema must not invent a value ------------------------------------


def test_validating_minimal_climate_input_does_not_add_a_threshold() -> None:
    """The falsifying assertion: this key was materialised by the default."""
    validated = CLIMATE_OPTIONS(dict(MINIMAL_CLIMATE))
    assert CONF_OUTSIDE_THRESHOLD not in validated


def test_an_explicit_threshold_still_validates_and_survives() -> None:
    """Removing the default must not stop the field working when it is used."""
    validated = CLIMATE_OPTIONS({**MINIMAL_CLIMATE, CONF_OUTSIDE_THRESHOLD: 18})
    assert validated[CONF_OUTSIDE_THRESHOLD] == 18


# --- the migration must clean up what is already stored ---------------------


def test_migration_removes_a_stored_zero() -> None:
    """Existing entries carry the materialised 0; the schema fix cannot reach it."""
    hass = _hass()
    entry = _entry({CONF_TEMP_LOW: 21, CONF_OUTSIDE_THRESHOLD: 0})

    assert _run(async_migrate_entry(hass, entry)) is True

    assert hass.config_entries.updates == [{CONF_TEMP_LOW: 21}]
    assert CONF_OUTSIDE_THRESHOLD not in entry.options


def test_migration_leaves_a_deliberate_threshold_alone() -> None:
    """Only 0 was unreachable as a choice. Every other value is the user's."""
    hass = _hass()
    entry = _entry({CONF_OUTSIDE_THRESHOLD: 25})

    assert _run(async_migrate_entry(hass, entry)) is True

    assert hass.config_entries.updates == []
    assert entry.options[CONF_OUTSIDE_THRESHOLD] == 25


def test_migration_is_idempotent_for_an_already_clean_entry() -> None:
    """Reloading a migrated entry must not rewrite it."""
    hass = _hass()
    entry = _entry({CONF_TEMP_LOW: 21})

    assert _run(async_migrate_entry(hass, entry)) is True

    assert hass.config_entries.updates == []


# --- the user must be able to clear one -------------------------------------


def test_a_blank_submission_clears_a_previously_stored_threshold() -> None:
    """Without this, removing the default made the field write-once.

    A blank field omits the key entirely, so `self.options.update(user_input)`
    would leave the stored value in place and no UI path could remove it.
    """
    flow = _options_flow({**MINIMAL_CLIMATE, CONF_OUTSIDE_THRESHOLD: 18})

    _run(flow.async_step_climate(dict(MINIMAL_CLIMATE)))

    assert flow.options[CONF_OUTSIDE_THRESHOLD] is None


def test_a_submitted_threshold_is_still_stored() -> None:
    """The clearing path must not swallow a real value."""
    flow = _options_flow(MINIMAL_CLIMATE)

    _run(flow.async_step_climate({**MINIMAL_CLIMATE, CONF_OUTSIDE_THRESHOLD: 18}))

    assert flow.options[CONF_OUTSIDE_THRESHOLD] == 18


# --- an inverted comfort band is a misconfiguration, not a season overlap ----


def test_options_flow_rejects_a_comfort_band_that_is_not_a_band() -> None:
    """temp_low >= temp_high disables a season; say so instead of accepting it."""
    flow = _options_flow(MINIMAL_CLIMATE)

    result = _run(
        flow.async_step_climate(
            {**MINIMAL_CLIMATE, CONF_TEMP_LOW: 25, CONF_TEMP_HIGH: 20}
        )
    )

    assert result["type"] == "form"
    assert result["errors"] == {CONF_TEMP_HIGH: "temp_high_not_above_low"}
    assert flow.options[CONF_TEMP_LOW] == 21, "rejected input must not be stored"


def test_config_flow_rejects_a_comfort_band_that_is_not_a_band() -> None:
    """Both flows write the same options, so both need the same guard."""
    flow = ConfigFlowHandler()
    flow.config = {}

    result = _run(
        flow.async_step_climate(
            {**MINIMAL_CLIMATE, CONF_TEMP_LOW: 25, CONF_TEMP_HIGH: 25}
        )
    )

    assert result["type"] == "form"
    assert result["errors"] == {CONF_TEMP_HIGH: "temp_high_not_above_low"}
    assert flow.config == {}


def test_an_ordinary_band_is_accepted() -> None:
    """Guard the guard: the common case must still get through."""
    flow = ConfigFlowHandler()
    flow.config = {}
    flow.async_step_update = _accepted

    result = _run(flow.async_step_climate(dict(MINIMAL_CLIMATE)))

    assert result == {"type": "accepted"}
    assert flow.config[CONF_TEMP_LOW] == 21


async def _accepted():
    return {"type": "accepted"}


def test_the_stored_zero_is_what_made_a_cold_morning_look_like_summer() -> None:
    """Document the consequence the schema fix removes.

    Not a regression test for calculation.py -- nothing there changed, and the
    None branch already behaved correctly (calculation.py:398, :417). It pins
    the contrast that makes the migration worth running: same weather, same
    thresholds, and the only difference is whether a 0 got persisted.
    """
    from tests.test_season_exclusivity import _climate

    march_morning = {
        "temp_low": 21.0,
        "temp_high": 25.0,
        "outside": 5.0,
        "inside": 19.0,
        "forecast": 8.0,
    }

    unset = _climate(**march_morning, outside_threshold=None)
    stored_zero = _climate(**march_morning, outside_threshold=0)

    assert unset.is_summer is False, "an unset gate must not reject heat in March"
    assert stored_zero.is_summer is True, "0 gated nothing: 8 > 0 + 2"
