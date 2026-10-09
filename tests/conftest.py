"""Shared fixtures: every test runs as no agent, whichever agent runs the suite."""

from __future__ import annotations

import pytest

# The markers feed_block.agents reads; an agent running pytest would set some.
AGENT_MARKERS = ("FEED_BLOCK_AGENT", "CLAUDECODE", "CODEX_THREAD_ID", "CODEX_SANDBOX", "PI_SESSION_FILE")


@pytest.fixture(autouse=True)
def no_agent(monkeypatch):
    for name in AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)
