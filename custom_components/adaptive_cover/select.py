"""Select platform for Adaptive Cover."""

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

OPTIONS = [
    "none",
    "15_min",
    "30_min",
    "60_min",
    "120_min",
    "240_min",
]

# Maps select option <-> minutes stored in config_entry.options.
_OPTION_TO_MINUTES = {
    "none": 0,
    "15_min": 15,
    "30_min": 30,
    "60_min": 60,
    "120_min": 120,
    "240_min": 240,
}
_MINUTES_TO_OPTION = {v: k for k, v in _OPTION_TO_MINUTES.items()}

# Older builds stored "until sunset" as 9999 minutes.
_LEGACY_SUNSET_MINUTES = 9999


def total_minutes_from_duration(duration: dict | None) -> int | None:
    """Total minutes represented by a stored duration dict.

    The options flow stores DurationSelector output, which can carry days/
    hours/minutes/seconds. Reading only the "minutes" key misrepresents
    hours-based durations (e.g. {"hours": 3, "minutes": 0} is 180 minutes,
    not 0). Returns None if the dict is unusable.
    """
    if not isinstance(duration, dict):
        return None
    total = 0
    for key, factor in (("days", 1440), ("hours", 60), ("minutes", 1)):
        value = duration.get(key, 0)
        try:
            total += int(value) * factor
        except (TypeError, ValueError):
            return None
    if duration.get("minutes") == _LEGACY_SUNSET_MINUTES:
        return 240
    return total


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([AdaptiveCoverOverrideSelect(coordinator, config_entry)])


class AdaptiveCoverOverrideSelect(CoordinatorEntity, SelectEntity):
    """Persistent manual-override duration select."""

    _attr_has_entity_name = True
    _attr_translation_key = "manual_override"
    _attr_icon = "mdi:timer-cog"

    def __init__(self, coordinator, config_entry) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_override_duration"
        self._attr_options = OPTIONS

        duration_dict = config_entry.options.get(
            "manual_override_duration", {"minutes": 15}
        )
        minutes = total_minutes_from_duration(duration_dict)
        # Durations set via the options-flow DurationSelector (e.g. 3 h,
        # 12 h) have no matching select option. Show no selection rather
        # than lying (a 3-hour duration used to display as "none"), and
        # never rewrite the stored value unless the user actively picks
        # an option here.
        self._attr_current_option = _MINUTES_TO_OPTION.get(minutes)

    @property
    def device_info(self) -> DeviceInfo:
        """Bind this entity to the adaptive_cover device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self.config_entry.data.get("name", "Adaptive Cover"),
        )

    async def async_select_option(self, option: str) -> None:
        """Persist the selected duration to config_entry.options."""
        self._attr_current_option = option
        minutes = _OPTION_TO_MINUTES.get(option, 60)

        new_options = dict(self.config_entry.options)
        new_options["manual_override_duration"] = {"minutes": minutes}

        self.hass.config_entries.async_update_entry(
            self.config_entry, options=new_options
        )
        self.async_write_ha_state()
