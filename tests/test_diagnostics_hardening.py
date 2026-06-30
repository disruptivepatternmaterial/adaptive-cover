"""Diagnostics hardening regression tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.adaptive_cover.const import CONF_ENTITIES, CONF_TEMP_ENTITY, DOMAIN
from custom_components.adaptive_cover.diagnostics import async_get_config_entry_diagnostics


def _run(coro):
    """Run async test helpers without pytest-asyncio."""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_diagnostics_runtime_uses_counts_not_entity_ids() -> None:
    """Runtime diagnostics should avoid exposing raw entity identifiers."""
    coordinator = SimpleNamespace(
        last_update_success=True,
        _switches_restored=True,
        control_method="summer",
        manager=SimpleNamespace(
            manual_controlled=["cover.kitchen", "cover.office"],
        ),
        is_window_open=False,
        wait_for_target={"cover.kitchen": True, "cover.office": False},
        target_call={"cover.kitchen": 80},
    )
    entry_id = "entry-123"
    hass = SimpleNamespace(data={DOMAIN: {entry_id: coordinator}})
    config_entry = SimpleNamespace(
        entry_id=entry_id,
        data={"name": "Kitchen", CONF_ENTITIES: ["cover.kitchen"]},
        options={CONF_TEMP_ENTITY: "sensor.kitchen_temperature"},
    )

    def _fake_redact_data(data, redact):
        redacted = dict(data)
        for key in redact:
            if key in redacted:
                redacted[key] = "**REDACTED**"
        return redacted

    with patch(
        "custom_components.adaptive_cover.diagnostics.async_redact_data",
        side_effect=_fake_redact_data,
    ):
        diagnostics = _run(async_get_config_entry_diagnostics(hass, config_entry))
    runtime = diagnostics["runtime"]

    assert runtime["manual_controlled_count"] == 2
    assert runtime["wait_for_target_count"] == 2
    assert runtime["wait_for_target_active_count"] == 1
    assert runtime["target_call_count"] == 1
    assert "manual_controlled" not in runtime
    assert "wait_for_target" not in runtime
    assert "target_call" not in runtime
    assert diagnostics["config_data"][CONF_ENTITIES] == "**REDACTED**"
    assert diagnostics["config_options"][CONF_TEMP_ENTITY] == "**REDACTED**"
