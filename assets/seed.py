"""A demo feed for the README previews: `python assets/seed.py PATH` (replaces PATH).

One day of four agents, in the shapes the automatic posts take, so the
screenshots show the real surface on data that is not anyone's own feed.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from feed_block.store import Store

# (minutes ago, fields), oldest first, so ids follow time.
POSTS = [
    (
        310,
        dict(agent="hermes", kind="session", title="Rate limits for the public API", repo="handover",
             branch="main", additions=212, deletions=40, session="h1", body="4 commits"),
    ),
    (
        190,
        dict(agent="hermes", kind="merge", title="merged PR #39 Rate limits", repo="handover", branch="main",
             link="https://github.com/thefullctx/handover/pull/39", session="h1"),
    ),
    (
        185,
        dict(agent="hermes", kind="deploy", title="deployed handover with fly", repo="handover",
             branch="main", session="h1"),
    ),
    (
        96,
        dict(agent="codex", kind="session", title="Login form validation", repo="handover",
             branch="feature/login", additions=88, deletions=12, status="failed", session="x1",
             body="tests still failing · pytest · 3 failed · 2 commits"),
    ),
    (
        41,
        dict(agent="omp", kind="pr", title="opened PR #44 Retry queue for failed syncs", repo="handover",
             branch="feature/retry", link="https://github.com/thefullctx/handover/pull/44", session="o1"),
    ),
    (
        38,
        dict(agent="omp", kind="session", title="Added a retry queue for failed syncs", repo="handover",
             branch="feature/retry", additions=340, deletions=52, session="o1", body="3 commits"),
    ),
    (
        22,
        dict(agent="omp", kind="session", title="Cleaned up the sync logging", repo="handover",
             branch="main", additions=40, deletions=88, session="o2", body="uncommitted"),
    ),
    (
        6,
        dict(agent="claude-code", kind="session", title="Wired offline mode into the sync worker",
             repo="feed-block", branch="main", additions=486, deletions=139, session="c1",
             body="5 commits"),
    ),
]  # fmt: skip


def seed(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    now = int(time.time())
    with Store(path) as store:
        made = [(minutes, store.post(**fields)) for minutes, fields in POSTS]
    db = sqlite3.connect(path)
    with db:
        for minutes, post in made:
            db.execute("UPDATE posts SET created_at = ? WHERE id = ?", (now - minutes * 60, post.id))
    db.close()


if __name__ == "__main__":
    seed(Path(sys.argv[1]))
