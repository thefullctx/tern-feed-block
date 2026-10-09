"""Agent detection: the markers each agent leaves in the shells it runs."""

from __future__ import annotations

import pytest

from feed_block import agents


@pytest.mark.parametrize(
    ("env", "agent"),
    [
        ({}, None),
        ({"CLAUDECODE": "1"}, "claude-code"),
        ({"CODEX_THREAD_ID": "t-1"}, "codex"),
        ({"CODEX_SANDBOX": "seatbelt"}, "codex"),
        ({"PI_SESSION_FILE": "/Users/x/.omp/agent/sessions/a.jsonl"}, "omp"),
        ({"PI_SESSION_FILE": "/Users/x/.pi/agent/sessions/a.jsonl"}, "pi"),
        ({"FEED_BLOCK_AGENT": " hermes ", "CLAUDECODE": "1"}, "hermes"),  # the override wins
        ({"FEED_BLOCK_AGENT": "  "}, None),
    ],
)
def test_detect(env, agent):
    assert agents.detect(env) == agent


@pytest.mark.parametrize(
    ("env", "session"),
    [
        ({}, None),
        ({"CLAUDE_CODE_SESSION_ID": "c-1"}, "c-1"),
        ({"CODEX_THREAD_ID": "t-1"}, "t-1"),
        ({"PI_SESSION_FILE": "/Users/x/.omp/agent/sessions/2026_abc.jsonl"}, "2026_abc"),
        ({"FEED_BLOCK_SESSION": "mine", "CODEX_THREAD_ID": "t-1"}, "mine"),
    ],
)
def test_session(env, session):
    assert agents.session(env) == session
