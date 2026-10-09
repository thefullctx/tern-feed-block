"""Which agent is running a command, from the environment it leaves behind.

A git hook or `feed post` runs inside the agent's shell, so the agent's own
markers are there to read. `FEED_BLOCK_AGENT` always wins; an agent that sets
no marker (Hermes, today) names itself through it or through the MCP `post`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


def detect(env: Mapping[str, str] | None = None) -> str | None:
    """The agent's name, or None when no agent's marker is set."""
    env = os.environ if env is None else env
    named = env.get("FEED_BLOCK_AGENT", "").strip()
    if named:
        return named
    if env.get("CLAUDECODE") == "1":
        return "claude-code"
    if env.get("CODEX_THREAD_ID") or env.get("CODEX_SANDBOX"):
        return "codex"
    # OMP is a fork of pi and keeps its variables; the session file says which one.
    session_file = env.get("PI_SESSION_FILE", "")
    if session_file:
        return "omp" if "/.omp/" in session_file else "pi"
    return None
