#
# Opening hours: free text in, per-weekday intervals out (decision 003).
#
# Accepted shapes (case-insensitive, segments separated by ';' or '|'):
#   "Mon-Fri 8:00-17:00"
#   "Mon-Fri 8:00-12:00, 13:00-18:00; Sat 9-13; Sun closed"
#   "Mon,Wed,Fri 7:30am-3pm"
#   "Daily 9:00-21:00"
# Anything else raises HoursError: an unparseable string is an error, never a
# silent pass-through. Times are datetime.time, so comparisons are never lexicographic.
#

import re
from datetime import time

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DAY_ALIASES = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
_RANGE = re.compile(rf"^{_TIME}\s*[-–]\s*{_TIME}$", re.I)


class HoursError(ValueError):
    pass


def _day(token: str) -> int:
    key = token.strip().lower().rstrip(".")
    if key not in _DAY_ALIASES:
        raise HoursError(f"unknown day '{token}'")
    return _DAY_ALIASES[key]


def _days(spec: str) -> list:
    spec = spec.strip().lower()
    if spec in ("daily", "every day", "mon-sun"):
        return list(range(7))
    if spec == "weekdays":
        return list(range(5))
    if spec == "weekends":
        return [5, 6]
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = (_day(x) for x in part.split("-", 1))
            if b < a:
                raise HoursError(f"day range runs backwards: '{part}'")
            out.extend(range(a, b + 1))
        else:
            out.append(_day(part))
    return out


def _clock(h: str, m: str, ampm: str, other_ampm: str) -> time:
    hour, minute = int(h), int(m or 0)
    ampm = (ampm or other_ampm or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour == 24 and minute == 0:
        return time(23, 59)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise HoursError(f"impossible time {h}:{m or '00'}")
    return time(hour, minute)


def _interval(text: str) -> tuple:
    m = _RANGE.match(text.strip())
    if not m:
        raise HoursError(f"unreadable time range '{text.strip()}'")
    h1, m1, ap1, h2, m2, ap2 = m.groups()
    # "9-1pm": the end's am/pm does not carry to the start unless it would invert.
    start = _clock(h1, m1, ap1, None)
    end = _clock(h2, m2, ap2, None)
    if end <= start and not ap1 and ap2:
        start = _clock(h1, m1, None, ap2)
    if end <= start:
        raise HoursError(f"closing time is not after opening time in '{text.strip()}'")
    return start, end


def parse_hours(text: str) -> dict:
    """Return {weekday 0..6: [(open, close), ...]}; days absent from the dict are closed."""
    if not text or not text.strip():
        raise HoursError("empty hours")
    week: dict = {}
    for segment in re.split(r"[;|]", text):
        segment = segment.strip()
        if not segment:
            continue
        m = re.match(r"^([A-Za-z][A-Za-z,\-\s.]*?)\s*(?::\s*)?(closed|\d.*)$", segment, re.I)
        if not m:
            raise HoursError(f"unreadable segment '{segment}'")
        days, ranges = _days(m.group(1)), m.group(2)
        if ranges.lower() == "closed":
            for d in days:
                week.pop(d, None)
            continue
        intervals = sorted(_interval(r) for r in ranges.split(","))
        for a, b in zip(intervals, intervals[1:]):
            if b[0] < a[1]:
                raise HoursError(f"overlapping intervals in '{segment}'")
        for d in days:
            week[d] = intervals
    if not week:
        raise HoursError("no open day at all")
    return week


def _fmt(t: time) -> str:
    suffix = "am" if t.hour < 12 else "pm"
    h = t.hour % 12 or 12
    return f"{h}{suffix}" if t.minute == 0 else f"{h}:{t.minute:02d}{suffix}"


def describe(week: dict) -> dict:
    """Spoken form per weekday, closed days included, e.g. {'Monday': '8am to 5pm'}."""
    return {
        DAY_NAMES[d]: (" and ".join(f"{_fmt(a)} to {_fmt(b)}" for a, b in week[d]) if d in week else "closed")
        for d in range(7)
    }


def summarize(week: dict) -> str:
    """One spoken line, consecutive identical days grouped:
    'Monday to Friday 8am to 5pm, Saturday 9am to 1pm, closed Sunday'."""
    spoken = describe(week)
    groups = []  # [first day, last day, text]
    for d in range(7):
        text = spoken[DAY_NAMES[d]]
        if groups and groups[-1][2] == text and groups[-1][1] == d - 1:
            groups[-1][1] = d
        else:
            groups.append([d, d, text])
    parts = []
    for a, b, text in groups:
        days = DAY_NAMES[a] if a == b else (f"{DAY_NAMES[a]} and {DAY_NAMES[b]}" if b == a + 1 else f"{DAY_NAMES[a]} to {DAY_NAMES[b]}")
        parts.append(f"closed {days}" if text == "closed" else f"{days} {text}")
    return ", ".join(parts)
