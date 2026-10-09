"""Session numbers on handles: only when one agent ran several sessions that day."""

from __future__ import annotations

import time

from feed_block.labels import handle, session_numbers
from feed_block.store import Post

NOW = int(time.time())


def post(id, agent="omp", session=None, created_at=NOW):
    return Post(id, agent, "session", "t", "", None, None, None, None, "ok", created_at, None, session)


def test_one_session_keeps_the_bare_handle():
    posts = [post(2, session="a"), post(1, session="a"), post(3, agent="codex", session="z")]
    numbers = session_numbers(posts)
    assert [handle(p, numbers) for p in posts] == ["@omp", "@omp", "@codex"]


def test_two_sessions_are_numbered_in_the_order_they_started():
    posts = [post(3, session="b", created_at=NOW), post(2, session="a", created_at=NOW - 60), post(1)]
    numbers = session_numbers(posts)
    assert [handle(p, numbers) for p in posts] == ["@omp²", "@omp¹", "@omp"]


def test_numbers_restart_each_day():
    yesterday = NOW - 2 * 86400
    posts = [
        post(3, session="b"),
        post(2, session="a", created_at=yesterday),
        post(1, session="c", created_at=yesterday),
    ]
    numbers = session_numbers(posts)
    assert [handle(p, numbers) for p in posts] == ["@omp", "@omp²", "@omp¹"]
