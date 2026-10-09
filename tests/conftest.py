"""Shared fixtures: every test runs as no agent, whichever agent runs the suite."""

from __future__ import annotations

import pytest

# The markers feed_block.agents reads; an agent running pytest would set some.
AGENT_MARKERS = (
    "FEED_BLOCK_AGENT",
    "FEED_BLOCK_SESSION",
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SANDBOX",
    "PI_SESSION_FILE",
)


@pytest.fixture(autouse=True)
def no_agent(monkeypatch, tmp_path):
    for name in AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)
    # Never the user's own settings: each test starts from the defaults.
    monkeypatch.setenv("FEED_BLOCK_CONFIG", str(tmp_path / "config.toml"))


@pytest.fixture
def commits_on(tmp_path):
    """The git hooks post every commit, not just merges."""
    (tmp_path / "config.toml").write_text("[git]\ncommits = true\n")
