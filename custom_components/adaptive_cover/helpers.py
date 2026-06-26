"""Helper functions."""

import datetime as dt

import pandas as pd
from dateutil import parser
from homeassistant.core import HomeAssistant, split_entity_id


def get_safe_state(hass: HomeAssistant, entity_id: str):
    """Get a safe state value if not available."""
    state = hass.states.get(entity_id)
    if not state or state.state in ["unknown", "unavailable"]:
        return None
    return state.state


def get_domain(entity: str):
    """Get domain of entity."""
    if entity is not None:
        domain, object_id = split_entity_id(entity)
        return domain


def get_timedelta_str(string: str):
    """Convert string to timedelta."""
    if string is not None:
        return pd.to_timedelta(string)


def get_datetime_from_str(string: str):
    """Convert datetime string to a timezone-naive local datetime.

    Parses the string respecting any embedded timezone information and then
    converts to the local wall-clock time (naive), which is what the rest of
    the coordinator uses for time comparisons.

    Returns None (rather than raising) when the string cannot be parsed, so
    callers that check for None before doing datetime arithmetic are safe even
    when a generic sensor (e.g. a non-datetime input_text) is configured for
    the start/end time slot.
    """
    if string is None:
        return None
    try:
        parsed = parser.parse(string)
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(tz=None).replace(tzinfo=None)
    return parsed


def get_last_updated(entity_id: str, hass: HomeAssistant):
    """Get last updated attribute from entity."""
    if entity_id is not None:
        if hass.states.get(entity_id):
            return hass.states.get(entity_id).last_updated


def check_time_passed(time: dt.datetime):
    """Check if time is passed for datetime.time()."""
    now = dt.datetime.now().time()
    return now >= time.time()


def dt_check_time_passed(time: dt.datetime):
    """Check if time is passed today for UTC datetime."""
    now = dt.datetime.now(dt.UTC)
    if now.date() == time.date():
        return now.time() > time.time()
    return True
