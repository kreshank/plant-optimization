"""Time parsing and calendar helpers (no plant-specific constants)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plant_sim.config_models import CalendarConfig

WEEKDAY_MAP = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def parse_time_of_day(value: str) -> time:
    """Parse HH:MM or HH:MM:SS."""
    parts = value.strip().split(":")
    if len(parts) == 2:
        h, m = int(parts[0]), int(parts[1])
        return time(h, m)
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        return time(h, m, s)
    raise ValueError(f"Invalid time: {value}")


def time_to_minutes(t: time) -> float:
    return t.hour * 60 + t.minute + t.second / 60.0


def minutes_to_time(minutes: float) -> time:
    minutes = minutes % (24 * 60)
    h = int(minutes // 60)
    m = int(minutes % 60)
    s = int(round((minutes % 1) * 60))
    return time(h, m, s)


def format_minutes(minutes: float) -> str:
    t = minutes_to_time(minutes)
    return t.strftime("%H:%M")


def operating_weekdays(calendar: CalendarConfig) -> set[int]:
    return {WEEKDAY_MAP[d.lower()] for d in calendar.operating_days}


def is_operating_day(weekday: int, calendar: CalendarConfig) -> bool:
    return weekday in operating_weekdays(calendar)


def day_open_minutes(calendar: CalendarConfig) -> float:
    return time_to_minutes(parse_time_of_day(calendar.day_open_time))


def wash_cutoff_minutes(calendar: CalendarConfig) -> float:
    return time_to_minutes(parse_time_of_day(calendar.wash_cutoff_time))


def deadline_minutes(value: str) -> float:
    return time_to_minutes(parse_time_of_day(value))


def sim_minutes_for_clock(clock_minutes: float, day_index: int, open_minutes: float) -> float:
    """Map wall-clock minutes within a day to simulation minutes from week start."""
    return day_index * 24 * 60 + clock_minutes


def clock_from_sim(sim_minutes: float) -> tuple[int, float]:
    """Return (day_index, minutes_within_day)."""
    day_index = int(sim_minutes // (24 * 60))
    within = sim_minutes % (24 * 60)
    return day_index, within
