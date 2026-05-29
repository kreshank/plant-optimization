"""Time parsing and calendar helpers."""

from __future__ import annotations

from datetime import time

from plant_sim.config_models import CalendarConfig, CalendarBreak, StageShift

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


def clock_from_sim(sim_minutes: float) -> tuple[int, float]:
    day_index = int(sim_minutes // (24 * 60))
    within = sim_minutes % (24 * 60)
    return day_index, within


def break_intervals_minutes(calendar: CalendarConfig) -> list[tuple[float, float]]:
    return [
        (time_to_minutes(parse_time_of_day(b.start)), time_to_minutes(parse_time_of_day(b.end)))
        for b in calendar.breaks
    ]


def minutes_until_break_ends(within_day: float, breaks: list[tuple[float, float]]) -> float:
    for start, end in breaks:
        if start <= within_day < end:
            return end - within_day
    return 0.0


def delay_for_shift(within_day: float, shift: StageShift | None) -> float:
    if shift is None:
        return 0.0
    start = time_to_minutes(parse_time_of_day(shift.start))
    end = time_to_minutes(parse_time_of_day(shift.end))
    if within_day < start:
        return start - within_day
    if within_day >= end:
        return (24 * 60 - within_day) + start
    return 0.0
