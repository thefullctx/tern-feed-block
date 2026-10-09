"""The local feed store: one SQLite file, one table, no server.

Any process may write (the MCP server, the CLI, a git hook) while the Tern
surface reads: WAL mode plus a busy timeout is the whole concurrency story.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# What a post can be about, and how it ended. Anything else is refused here,
# so every writer (MCP, CLI, hook) and the surface agree on the vocabulary.
KINDS = ("commit", "pr", "merge", "test", "deploy", "note")
STATUSES = ("ok", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    repo TEXT,
    link TEXT,
    additions INTEGER,
    deletions INTEGER,
    status TEXT NOT NULL DEFAULT 'ok',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_recent ON posts (id DESC);
CREATE INDEX IF NOT EXISTS posts_agent ON posts (agent, id DESC);
"""

# How many posts feed list and the surface show when no limit is given.
DEFAULT_LIMIT = 50
# A hard cap, so one client cannot ask the pane to render the whole file.
MAX_LIMIT = 500


def default_db_path() -> Path:
    """`~/.feed-block/feed.db`, or `$FEED_BLOCK_DB` (tests, experiments)."""
    override = os.environ.get("FEED_BLOCK_DB")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".feed-block" / "feed.db"


@dataclass(frozen=True)
class Post:
    """One post, exactly the fields the feed renders."""

    id: int
    agent: str
    kind: str
    title: str
    body: str
    repo: str | None
    link: str | None
    additions: int | None
    deletions: int | None
    status: str
    created_at: int

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def diffstat(self) -> str | None:
        """`+120 −34`, or None when there is no diff to show (none given, or +0 −0)."""
        if not self.additions and not self.deletions:
            return None
        return f"+{self.additions or 0} −{self.deletions or 0}"

    def dict(self) -> dict:
        return {
            "id": self.id,
            "agent": self.agent,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "repo": self.repo,
            "link": self.link,
            "additions": self.additions,
            "deletions": self.deletions,
            "status": self.status,
            "created_at": self.created_at,
        }


def _agent(agent: str) -> str:
    agent = str(agent or "").strip()
    if not agent:
        raise ValueError("agent is required (e.g. 'hermes')")
    return agent


def _title(title: str) -> str:
    title = str(title or "").strip()
    if not title:
        raise ValueError("title is required")
    # One line on the rail; bodies carry the rest.
    return title.splitlines()[0].strip()


def _counts(value: object, field: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{field} must be a non-negative integer")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a non-negative integer") from None
    if number < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return number


def _where(agent: str | None, agents: Iterable[str] | None) -> tuple[str, list[str]]:
    """The WHERE clause for one agent, or any of several; empty when neither is given."""
    names = [str(agent).strip()] if agent else []
    names += [str(name).strip() for name in agents or ()]
    if not names:
        return "", []
    return f" WHERE agent IN ({', '.join('?' * len(names))})", names


class Store:
    """Posts, newest first. Safe to share across processes on one machine."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=10.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writing

    def post(
        self,
        *,
        agent: str,
        kind: str,
        title: str,
        body: str = "",
        repo: str | None = None,
        link: str | None = None,
        additions: int | None = None,
        deletions: int | None = None,
        status: str = "ok",
    ) -> Post:
        """Appends one post and returns it. Refuses an unknown kind or status."""
        kind = str(kind or "").strip().lower()
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)} (got {kind!r})")
        status = str(status or "ok").strip().lower()
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)} (got {status!r})")
        post = Post(
            id=0,
            agent=_agent(agent),
            kind=kind,
            title=_title(title),
            body=str(body or ""),
            repo=str(repo).strip() or None if repo else None,
            link=str(link).strip() or None if link else None,
            additions=_counts(additions, "additions"),
            deletions=_counts(deletions, "deletions"),
            status=status,
            created_at=int(time.time()),
        )
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO posts (agent, kind, title, body, repo, link, additions, deletions,"
                " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    post.agent,
                    post.kind,
                    post.title,
                    post.body,
                    post.repo,
                    post.link,
                    post.additions,
                    post.deletions,
                    post.status,
                    post.created_at,
                ),
            )
        return Post(**{**post.dict(), "id": int(cursor.lastrowid or 0)})

    # -- reading

    def list(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        agent: str | None = None,
        agents: Iterable[str] | None = None,
    ) -> list[Post]:
        """Newest first, optionally one agent's posts (or any of several agents') only."""
        try:
            count = int(limit)
        except (TypeError, ValueError):
            raise ValueError("limit must be an integer") from None
        count = max(1, min(count, MAX_LIMIT))
        where, params = _where(agent, agents)
        rows = self._db.execute(
            f"SELECT * FROM posts{where} ORDER BY id DESC LIMIT ?", [*params, count]
        ).fetchall()
        return [Post(**dict(row)) for row in rows]

    def agents(self) -> list[str]:
        """Every agent that has posted, most recent first."""
        rows = self._db.execute(
            "SELECT agent, MAX(id) AS last FROM posts GROUP BY agent ORDER BY last DESC"
        ).fetchall()
        return [str(row["agent"]) for row in rows]

    def latest_id(self) -> int:
        """The id of the newest post, or 0 when the feed is empty."""
        row = self._db.execute("SELECT MAX(id) AS m FROM posts").fetchone()
        return int(row["m"] or 0)

    def has_link(self, link: str) -> bool:
        """Whether a post with this link exists: the hook's idempotence check."""
        row = self._db.execute("SELECT 1 FROM posts WHERE link = ? LIMIT 1", (link,)).fetchone()
        return row is not None

    def count(self, *, agents: Iterable[str] | None = None) -> int:
        """How many posts, optionally from these agents only."""
        where, params = _where(None, agents)
        row = self._db.execute(f"SELECT COUNT(*) AS n FROM posts{where}", params).fetchone()
        return int(row["n"])
