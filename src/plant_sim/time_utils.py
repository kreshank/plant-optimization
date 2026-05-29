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
    """Convert minutes-since-midnight to time; tolerates float drift."""
    minutes = minutes % (24 * 60)
    total_seconds = int(round(minutes * 60)) % (24 * 3600)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
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


def operating_window_bounds(
    first_calendar_day: int,
    last_calendar_day: int,
    calendar: CalendarConfig,
) -> tuple[float, float]:
    """Sim-minute range from first day open through last day wash cutoff."""
    start = first_calendar_day * 24 * 60 + day_open_minutes(calendar)
    end = last_calendar_day * 24 * 60 + wash_cutoff_minutes(calendar)
    return start, end


def operating_window_minutes(calendar: CalendarConfig) -> float:
    """Length of one operating day (open → wash cutoff)."""
    return max(0.0, wash_cutoff_minutes(calendar) - day_open_minutes(calendar))


def deadline_minutes(value: str) -> float:
    return time_to_minutes(parse_time_of_day(value))


def clock_from_sim(sim_minutes: float) -> tuple[int, float]:
    day_index = int(sim_minutes // (24 * 60))
    within = sim_minutes % (24 * 60)
    return day_index, within


DAY_NAMES_BY_INDEX = [k for k, v in sorted(WEEKDAY_MAP.items(), key=lambda x: x[1])]


def sim_clock_display(
    sim_minutes: float,
    calendar: CalendarConfig,
    start_weekday: int = 0,
) -> dict[str, str | int | float]:
    """Calendar day index, weekday name, and HH:MM for UI playback."""
    day_index, within = clock_from_sim(sim_minutes)
    weekday_idx = (start_weekday + day_index) % 7
    weekday = DAY_NAMES_BY_INDEX[weekday_idx]
    operating = is_operating_day(weekday_idx, calendar)
    return {
        "sim_minutes": round(sim_minutes, 2),
        "calendar_day": day_index + 1,
        "weekday": weekday,
        "time_of_day": format_minutes(within),
        "operating": operating,
    }


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


def calendar_wait_minutes(
    within_day: float,
    shift: StageShift | None,
    breaks: list[tuple[float, float]],
) -> float:
    """Minutes until shift open and any calendar break ends (0 = may work)."""
    return max(
        minutes_until_break_ends(within_day, breaks),
        delay_for_shift(within_day, shift),
    )


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
