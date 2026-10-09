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
KINDS = ("commit", "pr", "merge", "test", "deploy", "note", "session")
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
    created_at INTEGER NOT NULL,
    branch TEXT,
    session TEXT
);
CREATE INDEX IF NOT EXISTS posts_recent ON posts (id DESC);
CREATE INDEX IF NOT EXISTS posts_agent ON posts (agent, id DESC);
-- What an agent session has done so far, per repository it worked in: the
-- automatic posts read this to decide whether the session has anything to say.
CREATE TABLE IF NOT EXISTS sessions (
    session TEXT NOT NULL,
    root TEXT NOT NULL,
    agent TEXT NOT NULL,
    base TEXT,
    started_at INTEGER NOT NULL,
    tests_ok INTEGER,
    tests_summary TEXT,
    post_id INTEGER,
    PRIMARY KEY (session, root)
);
"""

# Columns added after the first release; an older feed gains them on open.
ADDED_COLUMNS = {"branch": "TEXT", "session": "TEXT"}

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
    branch: str | None = None
    session: str | None = None

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
            "branch": self.branch,
            "session": self.session,
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


def _optional(value: object) -> str | None:
    """A trimmed string, or None for nothing or blanks."""
    return str(value).strip() or None if value else None


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
        have = {row["name"] for row in self._db.execute("PRAGMA table_info(posts)")}
        for name, kind in ADDED_COLUMNS.items():
            if name not in have:
                self._db.execute(f"ALTER TABLE posts ADD COLUMN {name} {kind}")
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
        branch: str | None = None,
        session: str | None = None,
    ) -> Post:
        """Appends one post and returns it. Refuses an unknown kind or status."""
        made = self._build(
            agent=agent, kind=kind, title=title, body=body, repo=repo, link=link, additions=additions,
            deletions=deletions, status=status, branch=branch, session=session,
        )  # fmt: skip
        with self._db:
            return self._insert(made)

    def _build(
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
        branch: str | None = None,
        session: str | None = None,
    ) -> Post:
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
            repo=_optional(repo),
            link=_optional(link),
            additions=_counts(additions, "additions"),
            deletions=_counts(deletions, "deletions"),
            status=status,
            created_at=int(time.time()),
            branch=_optional(branch),
            session=_optional(session),
        )
        return post

    def _insert(self, post: Post) -> Post:
        fields = {name: value for name, value in post.dict().items() if name != "id"}
        cursor = self._db.execute(
            f"INSERT INTO posts ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
            list(fields.values()),
        )
        return Post(**{**post.dict(), "id": int(cursor.lastrowid or 0)})

    def replace_session_post(self, session: str, root: str, **fields) -> Post:
        """Posts a session's summary in place of its previous one, in one step.

        Two events of one session (a reply ending as the session closes) can land
        at once; the write lock makes the second replace the first, never sit beside it.
        """
        made = self._build(session=session, **fields)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT post_id FROM sessions WHERE session = ? AND root = ?", (session, root)
            ).fetchone()
            new = self._insert(made)
            if row and row["post_id"]:
                self._db.execute("DELETE FROM posts WHERE id = ?", (row["post_id"],))
            self._db.execute(
                "UPDATE sessions SET post_id = ? WHERE session = ? AND root = ?", (new.id, session, root)
            )
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        return new

    # -- agent sessions

    def session_state(self, session: str, root: str) -> dict | None:
        """What this session has recorded for one repository, or None."""
        row = self._db.execute(
            "SELECT * FROM sessions WHERE session = ? AND root = ?", (session, root)
        ).fetchone()
        return dict(row) if row else None

    def session_roots(self, session: str) -> list[dict]:
        """Every repository this session has worked in."""
        rows = self._db.execute("SELECT * FROM sessions WHERE session = ? ORDER BY started_at", (session,))
        return [dict(row) for row in rows]

    def open_session(self, session: str, root: str, agent: str, base: str | None) -> dict:
        """Starts tracking a session in one repository (a no-op when it already is)."""
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO sessions (session, root, agent, base, started_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (session, root, agent, base, int(time.time())),
            )
        return self.session_state(session, root) or {}

    def update_session(self, session: str, root: str, **fields: object) -> None:
        """Records what a session did: its latest test result, its summary post."""
        allowed = {"tests_ok", "tests_summary", "post_id"}
        if not fields or not set(fields) <= allowed:
            raise ValueError(f"session fields are {', '.join(sorted(allowed))}")
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._db:
            self._db.execute(
                f"UPDATE sessions SET {assignments} WHERE session = ? AND root = ?",
                [*fields.values(), session, root],
            )

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

    def since(self, timestamp: int) -> tuple[dict[str, int], int]:
        """Posts per agent, and failed posts, from `timestamp` on: the strip's "today"."""
        rows = self._db.execute(
            "SELECT agent, COUNT(*) AS n, SUM(status = 'failed') AS failed FROM posts"
            " WHERE created_at >= ? GROUP BY agent",
            (timestamp,),
        ).fetchall()
        return {str(row["agent"]): int(row["n"]) for row in rows}, sum(
            int(row["failed"] or 0) for row in rows
        )

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
