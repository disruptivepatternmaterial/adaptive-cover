"""Stub all external dependencies so adaptive-cover tests run without HA installed."""
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _mod(name: str, **attrs) -> ModuleType:
    m = sys.modules.get(name) or ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# stdlib-adjacent stubs
import numpy as _np_real  # noqa: F401 — try real numpy; fall through if missing
try:
    import numpy  # noqa: F401
except ImportError:
    _mod("numpy")

try:
    import pytz  # noqa: F401
except ImportError:
    _mod("pytz", UTC=None, timezone=MagicMock())

# ---------------------------------------------------------------------------
# Home Assistant stubs
# ---------------------------------------------------------------------------
_mod("homeassistant")
_mod("homeassistant.config_entries", ConfigEntry=MagicMock)
_mod("homeassistant.const",
     ATTR_ENTITY_ID="entity_id",
     SERVICE_SET_COVER_POSITION="set_cover_position",
     SERVICE_SET_COVER_TILT_POSITION="set_cover_tilt_position",
)
_mod("homeassistant.core",
     HomeAssistant=MagicMock, Event=MagicMock,
     EventStateChangedData=MagicMock, State=MagicMock, callback=lambda f: f,
)
_mod("homeassistant.helpers")
_mod("homeassistant.helpers.event", async_track_point_in_time=MagicMock)
class _Generic:
    def __class_getitem__(cls, item):
        return cls

_mod("homeassistant.helpers.storage", Store=MagicMock)
_mod("homeassistant.helpers.update_coordinator",
     DataUpdateCoordinator=_Generic, CoordinatorEntity=_Generic)
_mod("homeassistant.components")
_mod("homeassistant.components.cover", DOMAIN="cover")

# ---------------------------------------------------------------------------
# Stub internal adaptive_cover submodules
# ---------------------------------------------------------------------------
import os as _os

_custom = _mod("custom_components")
_ac = _mod("custom_components.adaptive_cover")
_ac.__path__ = [
    _os.path.join(_os.path.dirname(__file__), "..", "custom_components", "adaptive_cover")
]
_ac.__package__ = "custom_components.adaptive_cover"
_custom.adaptive_cover = _ac  # allow patch() to navigate custom_components.adaptive_cover

# Stub the submodules that coordinator.py imports internally
_mod("custom_components.adaptive_cover.config_context_adapter",
     ConfigContextAdapter=MagicMock)

_calc = _mod("custom_components.adaptive_cover.calculation")
for _name in ("AdaptiveHorizontalCover", "AdaptiveTiltCover", "AdaptiveVerticalCover",
              "ClimateCoverData", "ClimateCoverState", "NormalCoverState"):
    setattr(_calc, _name, MagicMock)

# Stub the full const module — use __getattr__ so any symbol resolves
_const = _mod("custom_components.adaptive_cover.const")
_const._LOGGER = MagicMock()
_const.LOGGER = MagicMock()
_const.DOMAIN = "adaptive_cover"
_const.DEFAULT_WINDOW_OPEN_HOLD = 1800
_const.DEFAULT_MAX_POSITION = 100
_const.DEFAULT_MIN_POSITION = 0

def _const_getattr(name):
    return name

_const.__getattr__ = _const_getattr

_mod("custom_components.adaptive_cover.helpers",
     get_datetime_from_str=MagicMock(return_value=None),
     get_last_updated=MagicMock(),
     get_safe_state=MagicMock(return_value=None),
)
