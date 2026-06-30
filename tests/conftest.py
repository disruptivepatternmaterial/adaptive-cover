"""Stub Home Assistant dependencies so tests run without HA installed."""

from __future__ import annotations

import datetime as dt
import math
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

UTC = getattr(dt, "UTC", dt.timezone.utc)  # noqa: UP017


def _mod(name: str, **attrs) -> ModuleType:
    m = sys.modules.get(name) or ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# stdlib-adjacent stubs
try:
    import numpy  # noqa: F401
except ImportError:
    _mod(
        "numpy",
        cos=math.cos,
        sin=math.sin,
        tan=math.tan,
        radians=math.radians,
        rad2deg=math.degrees,
        arctan=math.atan,
        sqrt=math.sqrt,
        maximum=lambda a, b: a if a > b else b,
        clip=lambda v, mn, mx: max(mn, min(mx, v)),
        interp=lambda x, xp, fp: fp[0] if x <= xp[0] else fp[-1],
    )

try:
    import pytz  # noqa: F401
except ImportError:
    _mod("pytz", UTC=None, timezone=MagicMock())

try:
    import pandas  # noqa: F401
except ImportError:
    _mod(
        "pandas",
        DataFrame=MagicMock,
        DatetimeIndex=list,
        date_range=MagicMock(return_value=[]),
        to_timedelta=MagicMock(return_value=dt.timedelta(0)),
    )

try:
    from dateutil import parser as _parser  # noqa: F401
except ImportError:
    dateutil_mod = _mod("dateutil")
    parser_mod = _mod(
        "dateutil.parser",
        parse=MagicMock(return_value=dt.datetime.now()),
    )
    dateutil_mod.parser = parser_mod


try:
    import voluptuous  # noqa: F401
except ImportError:
    class _VolSchema:
        """Simple voluptuous.Schema stand-in."""

        def __init__(self, schema, **_kwargs):
            self.schema = schema

        def __call__(self, value):
            return value

    def _vol_required(key, **_kwargs):
        return key

    def _vol_optional(key, **_kwargs):
        return key

    def _vol_coerce(cast_type):
        def _validator(value):
            try:
                return cast_type(value)
            except (TypeError, ValueError):
                return value

        return _validator

    def _vol_range(**_kwargs):
        return lambda value: value

    def _vol_all(*validators):
        def _validator(value):
            current = value
            for validator in validators:
                if callable(validator):
                    current = validator(current)
            return current

        return _validator

    _mod(
        "voluptuous",
        Schema=_VolSchema,
        Required=_vol_required,
        Optional=_vol_optional,
        Coerce=_vol_coerce,
        Range=_vol_range,
        All=_vol_all,
        UNDEFINED=object(),
    )


class _FlowBase:
    """Small stand-in for HA flow classes."""

    def add_suggested_values_to_schema(self, schema, suggested):  # noqa: ANN001
        return schema

    def async_show_form(self, step_id, data_schema=None, errors=None):  # noqa: ANN001
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_show_menu(self, step_id, menu_options):  # noqa: ANN001
        return {"type": "menu", "step_id": step_id, "menu_options": menu_options}

    def async_create_entry(self, title="", data=None, **kwargs):  # noqa: ANN001
        return {"type": "create_entry", "title": title, "data": data, **kwargs}


class _ConfigFlow(_FlowBase):
    """Small stand-in for HA ConfigFlow."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    async def async_set_unique_id(self, unique_id):
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        return None


class _OptionsFlow(_FlowBase):
    """Small stand-in for HA OptionsFlow."""


class _Selector:
    """Simple selector object."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __call__(self, value):
        return value


class _SelectorConfig:
    """Simple selector config object."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _DataUpdateCoordinator:
    """Small stand-in for HA DataUpdateCoordinator."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger=None, name=None, config_entry=None):  # noqa: ANN001
        self.hass = hass
        self.logger = logger
        self.name = name
        self.config_entry = config_entry
        self.data = None
        self.last_update_success = True

    async def async_config_entry_first_refresh(self):
        return None

    async def async_refresh(self):
        return None


class _CoordinatorEntity:
    """Small stand-in for HA CoordinatorEntity."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator=None):  # noqa: ANN001
        self.coordinator = coordinator

    async def async_added_to_hass(self):
        return None

    async def async_get_last_state(self):
        return None

    def async_write_ha_state(self):
        return None

    def schedule_update_ha_state(self):
        return None


class _Generic:
    def __class_getitem__(cls, item):
        return cls


class _Location:
    """Small stand-in for astral location."""

    def solar_azimuth(self, *_args, **_kwargs):
        return 180.0

    def solar_elevation(self, *_args, **_kwargs):
        return 45.0

    def sunset(self, *_args, **_kwargs):
        return dt.datetime.now(UTC)

    def sunrise(self, *_args, **_kwargs):
        return dt.datetime.now(UTC)


# ---------------------------------------------------------------------------
# Home Assistant stubs
# ---------------------------------------------------------------------------
_mod("homeassistant")
_mod(
    "homeassistant.config_entries",
    ConfigEntry=MagicMock,
    ConfigFlow=_ConfigFlow,
    OptionsFlow=_OptionsFlow,
)
_mod("homeassistant.const",
     ATTR_ENTITY_ID="entity_id",
     SERVICE_SET_COVER_POSITION="set_cover_position",
     SERVICE_SET_COVER_TILT_POSITION="set_cover_tilt_position",
     Platform=SimpleNamespace(
         SENSOR="sensor",
         SWITCH="switch",
         BINARY_SENSOR="binary_sensor",
         BUTTON="button",
         SELECT="select",
     ),
)
_mod("homeassistant.core",
     HomeAssistant=MagicMock, Event=MagicMock,
     EventStateChangedData=MagicMock, State=MagicMock, callback=lambda f: f,
     split_entity_id=lambda entity: entity.split(".", 1),
)
_mod("homeassistant.data_entry_flow", FlowResult=dict)
helpers_module = _mod("homeassistant.helpers")
_mod(
    "homeassistant.helpers.event",
    async_track_point_in_time=MagicMock,
    async_track_state_change_event=MagicMock,
)
_mod("homeassistant.helpers.storage", Store=MagicMock)
_mod(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=_DataUpdateCoordinator,
    CoordinatorEntity=_CoordinatorEntity,
    UpdateFailed=RuntimeError,
)
_mod(
    "homeassistant.helpers.selector",
    TextSelector=_Selector,
    NumberSelector=_Selector,
    EntitySelector=_Selector,
    SelectSelector=_Selector,
    TimeSelector=_Selector,
    DurationSelector=_Selector,
    BooleanSelector=_Selector,
    NumberSelectorConfig=_SelectorConfig,
    EntitySelectorConfig=_SelectorConfig,
    EntityFilterSelectorConfig=_SelectorConfig,
    SelectSelectorConfig=_SelectorConfig,
)
helpers_module.selector = sys.modules["homeassistant.helpers.selector"]
_mod("homeassistant.helpers.sun", get_astral_location=lambda hass: (_Location(), 0))
_mod("homeassistant.components")
_mod("homeassistant.components.cover", DOMAIN="cover")
_mod("homeassistant.components.diagnostics", async_redact_data=lambda data, _redact: data)
