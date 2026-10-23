"""Handles as the feed shows them: `@omp`, or `@omp¹` and `@omp²` for two sessions.

A number only appears when it tells two things apart: one agent with more
than one session on the same day. Sessions are numbered in the order they
began, so a number never changes as a session's summary moves to the top;
posts without a session keep the bare handle.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from .reltime import day_key
from .store import Post, Store

SUPERSCRIPTS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

# (agent, session, posted at, began at or None): one per post with a session.
Seen = tuple[str, str, int, int | None]


def session_numbers(posts: list[Post], seen: Iterable[Seen] | None = None) -> dict[int, int]:
    """Post id → its session's number that day, for agents with several sessions.

    `seen` is every session post of those days, not only the ones shown, oldest
    first, with when each session began; without it the given posts are all there is.
    """
    if seen is None:
        ordered = sorted(posts, key=lambda p: (p.created_at, p.id))
        seen = [(p.agent, p.session, p.created_at, None) for p in ordered if p.session]
    began: dict[tuple[str, dt.date], dict[str, tuple[int, int]]] = {}
    for agent, session, created_at, start in seen:
        day = began.setdefault((agent, day_key(created_at)), {})
        first = (start if start is not None else created_at, created_at)
        day[session] = min(day.get(session, first), first)
    numbers = {}
    for post in posts:
        day = began.get((post.agent, day_key(post.created_at)), {})
        if post.session in day and len(day) > 1:
            order = sorted(day, key=lambda session: day[session])
            numbers[post.id] = order.index(post.session) + 1
    return numbers


def numbers_for(posts: list[Post], store: Store | None) -> dict[int, int]:
    """Session numbers for the posts shown, counting every session of their days in the store."""
    if not posts or store is None:
        return session_numbers(posts)
    first = dt.datetime.combine(day_key(min(p.created_at for p in posts)), dt.time())
    return session_numbers(posts, store.sessions_since(int(first.timestamp())))


def handle(post: Post, numbers: dict[int, int]) -> str:
    """`@agent`, with its session's number when the day has several."""
    number = numbers.get(post.id)
    return f"@{post.agent}" + (str(number).translate(SUPERSCRIPTS) if number else "")
