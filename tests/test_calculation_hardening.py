"""Calculation hardening regression tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.adaptive_cover.calculation import ClimateCoverState


def test_climate_state_is_clipped_to_max() -> None:
    """Climate path should clamp out-of-range values above 100."""
    cover = SimpleNamespace(
        apply_max_position=False,
        apply_min_position=False,
        logger=MagicMock(),
    )
    climate = SimpleNamespace(blind_type="cover_blind")
    state = ClimateCoverState(cover, climate)
    state.normal_type_cover = MagicMock(return_value=140)

    assert state.get_state() == 100


def test_climate_state_is_clipped_to_min() -> None:
    """Climate path should clamp out-of-range values below 0."""
    cover = SimpleNamespace(
        apply_max_position=False,
        apply_min_position=False,
        logger=MagicMock(),
    )
    climate = SimpleNamespace(blind_type="cover_blind")
    state = ClimateCoverState(cover, climate)
    state.normal_type_cover = MagicMock(return_value=-25)

    assert state.get_state() == 0
