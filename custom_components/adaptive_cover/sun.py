"""Fetch sun data."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
from astral import sun as astral_sun
from homeassistant.core import HomeAssistant
from homeassistant.helpers.sun import get_astral_observer


def _utc(value):
    """Convert an aware datetime to UTC before handing it to astral.

    astral.sun.zenith_and_azimuth() takes the Julian day from the datetime's
    own date but the time of day from its UTC hour, so a Pacific evening
    timestamp gets today's date paired with tomorrow's hour. astral's Location
    class sidestepped that by converting to UTC first; without reproducing it
    here, solar elevation drifts by up to 0.39 degrees.
    """
    return value.astimezone(UTC)


class SunData:
    """Access local sun data."""

    def __init__(self, timezone, hass: HomeAssistant) -> None:  # noqa: D107
        self.hass = hass
        # An Observer already carries hass.config.elevation, so elevation is
        # no longer a separate per-call argument. Unlike the old
        # Location.sunset/sunrise calls, which silently used elevation 0,
        # sunrise/sunset now honour the configured elevation and therefore
        # agree with Core's own sun.sun entity.
        self.observer = get_astral_observer(hass)
        self.timezone = timezone

    @property
    def times(self) -> pd.DatetimeIndex:
        """Define time interval."""
        start_date = date.today()
        end_date = start_date + timedelta(days=1)

        times = pd.date_range(
            start=start_date, end=end_date, freq="5min", tz=self.timezone, name="time"
        )
        return times

    @property
    def solar_azimuth(self) -> list:
        """Create list with solar azimuth data per 5 minutes."""
        return self.solar_azimuth_for(self.times)

    def solar_azimuth_for(self, times) -> list:
        """Solar azimuth for a caller-supplied DatetimeIndex."""
        return [astral_sun.azimuth(self.observer, _utc(t)) for t in times]

    @property
    def solar_elevation(self) -> list:
        """Create list with solar elevation data per 5 minutes."""
        return self.solar_elevation_for(self.times)

    def solar_elevation_for(self, times) -> list:
        """Solar elevation for a caller-supplied DatetimeIndex."""
        return [astral_sun.elevation(self.observer, _utc(t)) for t in times]

    def sunset(self) -> datetime:
        """Fetch sunset time."""
        return astral_sun.sunset(self.observer, date.today())

    def sunrise(self) -> datetime:
        """Fetch sunrise time."""
        return astral_sun.sunrise(self.observer, date.today())

    # def df_today(self)-> pd.DataFrame:
    #     """Create dataframe with azimuth and elevation data"""
    #     df_today = pd.DataFrame({"azimuth":self.solar_azimuth, "elevation":self.solar_elevation})
    #     df_today = df_today.set_index(self.times)
    #     return df_today
