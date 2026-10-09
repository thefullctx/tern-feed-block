"""Time as the feed reads it: relative ages on the rail, day labels on dividers.

Local time, because the feed is a local timeline: the rail shows your clock,
not UTC. Month names are lowercase and unpadded so "oct 7" is one token.
"""

from __future__ import annotations

import calendar
import datetime as dt
import time


def relative(timestamp: int | float, now: float | None = None) -> str:
    """ "now", "45s", "12m", "3h", "6d", then "oct 7" (with a year when it differs)."""
    now = time.time() if now is None else now
    age = max(0, int(now - timestamp))
    if age < 5:
        return "now"
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86400:
        return f"{age // 3600}h"
    if age < 7 * 86400:
        return f"{age // 86400}d"
    day = dt.datetime.fromtimestamp(timestamp)
    stamp = f"{calendar.month_abbr[day.month].lower()} {day.day}"
    return stamp if day.year == dt.datetime.fromtimestamp(now).year else f"{stamp} {day.year}"


def day_label(timestamp: int | float, now: float | None = None) -> str:
    """A day divider's label: "today", "yesterday", else the calendar day."""
    now = time.time() if now is None else now
    today = dt.datetime.fromtimestamp(now).date()
    day = dt.datetime.fromtimestamp(timestamp).date()
    delta = (today - day).days
    if delta <= 0:
        return "today"
    if delta == 1:
        return "yesterday"
    stamp = f"{calendar.month_abbr[day.month].lower()} {day.day}"
    return stamp if day.year == today.year else f"{stamp} {day.year}"


def day_key(timestamp: int | float) -> dt.date:
    """The local calendar day a post belongs to."""
    return dt.datetime.fromtimestamp(timestamp).date()
