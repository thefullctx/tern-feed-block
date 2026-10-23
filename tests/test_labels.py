"""Session numbers on handles: only when one agent ran several sessions that day."""

from __future__ import annotations

import time

from feed_block.labels import handle, numbers_for, session_numbers
from feed_block.store import Post, Store

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


def test_a_replaced_summary_keeps_its_sessions_number(tmp_path):
    """Session a began first; its summary, replaced later, still reads ¹."""
    with Store(tmp_path / "feed.db") as store:
        store.open_session("a", "/r", "omp", None)
        store.post(agent="omp", kind="note", title="b's first", session="b")
        store.replace_session_post("a", "/r", agent="omp", kind="session", title="a's summary")
        store._db.execute("UPDATE sessions SET started_at = started_at - 600")
        store._db.commit()
        posts = store.list()
        numbers = numbers_for(posts, store)
        assert [handle(p, numbers) for p in posts] == ["@omp¹", "@omp²"]


def test_numbers_count_sessions_beyond_the_page(tmp_path):
    """Only b's post is shown, but a posted earlier that day: b stays ²."""
    with Store(tmp_path / "feed.db") as store:
        store.post(agent="omp", kind="note", title="a", session="a")
        store.post(agent="omp", kind="note", title="b", session="b")
        posts = store.list(limit=1)
        assert [handle(p, numbers_for(posts, store)) for p in posts] == ["@omp²"]
