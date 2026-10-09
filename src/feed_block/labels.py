"""Handles as the feed shows them: `@omp`, or `@omp¹` and `@omp²` for two sessions.

A number only appears when it tells two things apart: one agent with more
than one session on the same day. Sessions are numbered in the order they
first posted that day; posts without a session keep the bare handle.
"""

from __future__ import annotations

from .reltime import day_key
from .store import Post

SUPERSCRIPTS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def session_numbers(posts: list[Post]) -> dict[int, int]:
    """Post id → its session's number that day, for agents with several sessions."""
    order: dict[tuple[str, object], list[str]] = {}
    for post in sorted(posts, key=lambda p: (p.created_at, p.id)):
        if not post.session:
            continue
        seen = order.setdefault((post.agent, day_key(post.created_at)), [])
        if post.session not in seen:
            seen.append(post.session)
    numbers = {}
    for post in posts:
        seen = order.get((post.agent, day_key(post.created_at)), [])
        if post.session and len(seen) > 1:
            numbers[post.id] = seen.index(post.session) + 1
    return numbers


def handle(post: Post, numbers: dict[int, int]) -> str:
    """`@agent`, with its session's number when the day has several."""
    number = numbers.get(post.id)
    return f"@{post.agent}" + (str(number).translate(SUPERSCRIPTS) if number else "")
