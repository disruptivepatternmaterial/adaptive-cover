"""Regression: log records must name their own module, and a zone must not crash.

Both of these are small but user-visible. A shared logger named after `const`
made `logger:` filters on the real module silently match nothing, and an
unparseable zone state took the whole coordinator update down with it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.adaptive_cover import (
    button as button_mod,
    const as const_mod,
    coordinator as coordinator_mod,
)
from custom_components.adaptive_cover.calculation import ClimateCoverData

PACKAGE = "custom_components.adaptive_cover"


def test_const_no_longer_exports_a_shared_module_logger() -> None:
    """_LOGGER in const was named `...const` and used by three other modules."""
    assert not hasattr(const_mod, "_LOGGER"), (
        "a logger defined in const names every record after const"
    )
    assert const_mod.LOGGER.name == PACKAGE


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        (coordinator_mod, f"{PACKAGE}.coordinator"),
        (button_mod, f"{PACKAGE}.button"),
    ],
)
def test_each_module_logs_under_its_own_name(module, expected: str) -> None:  # noqa: ANN001
    """So `logger: logs: ...coordinator: debug` actually selects something."""
    assert module._LOGGER.name == expected
    # Still under the package, so package-level filtering keeps working.
    assert module._LOGGER.name.startswith(f"{PACKAGE}.")


def _climate_with_zone(state: str) -> ClimateCoverData:
    data = ClimateCoverData.__new__(ClimateCoverData)
    data.hass = MagicMock()
    data.logger = MagicMock()
    data.presence_entity = "zone.home"
    data.hass.states.get.side_effect = lambda _eid: SimpleNamespace(
        state=state, attributes={}
    )
    return data


@pytest.mark.parametrize("state", ["2", "1"])
def test_zone_occupant_count_is_read_as_presence(state: str) -> None:
    """The normal case: a zone state is a count of people in it."""
    assert _climate_with_zone(state).is_presence is True


def test_empty_zone_reports_no_presence() -> None:
    """Zero occupants is the whole point of the check."""
    assert _climate_with_zone("0").is_presence is False


@pytest.mark.parametrize("state", ["0.0", "home", "", "two"])
def test_unparseable_zone_state_assumes_presence_instead_of_raising(
    state: str,
) -> None:
    """int() on a user-supplied state used to raise straight through the update.

    Assuming presence matches the default for no presence sensor at all, and is
    the conservative choice: it keeps comfort handling rather than force-closing.
    """
    climate = _climate_with_zone(state)
    assert climate.is_presence is True
    assert any(
        "not a count" in str(call) for call in climate.logger.debug.call_args_list
    ), "the fallback should say why it fired"
