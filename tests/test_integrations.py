"""Agent wiring: Claude Code's events, and installs that undo exactly what they did."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from feed_block import cli, integrations
from feed_block.store import Store

COMMAND = ["/opt/feed/bin/feed"]


# -- Claude Code's events


def test_claude_events_become_autopost_events():
    common = {"session_id": "s1", "cwd": "/repo"}
    start = integrations.claude_event({**common, "hook_event_name": "SessionStart"})
    assert start == {"agent": "claude-code", "session": "s1", "cwd": "/repo", "event": "start"}

    ran = integrations.claude_event(
        {
            **common,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
            "tool_response": {"stdout": "92 passed", "stderr": ""},
        }
    )
    assert (ran["event"], ran["command"], ran["ok"], ran["output"].strip()) == (
        "command",
        "pytest",
        True,
        "92 passed",
    )

    failed = integrations.claude_event(
        {
            **common,
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
            "error": "Exit code 1\n3 failed",
        }
    )
    assert (failed["ok"], failed["output"]) == (False, "Exit code 1\n3 failed")

    turn = integrations.claude_event({**common, "hook_event_name": "Stop", "last_assistant_message": "Done."})
    assert (turn["event"], turn["message"]) == ("turn", "Done.")
    assert integrations.claude_event({**common, "hook_event_name": "SessionEnd"})["event"] == "end"


def test_claude_events_that_are_not_results_are_ignored():
    edit = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {}}
    assert integrations.claude_event(edit) is None
    assert integrations.claude_event({"hook_event_name": "UserPromptSubmit"}) is None


# -- installs


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".omp" / "agent").mkdir(parents=True)
    (home / ".codex").mkdir()
    (home / ".hermes").mkdir()
    return home


def apply(home, uninstall=False):
    changes, notes = integrations.plan(home, COMMAND, uninstall=uninstall)
    for change in changes:
        change.apply()
    return changes, notes


def test_install_and_uninstall_leave_every_config_as_it_was(home):
    theirs = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}, "model": "opus"}
    settings = home / ".claude" / "settings.json"
    settings.write_text(json.dumps(theirs))
    codex = home / ".codex" / "config.toml"
    codex.write_text('model = "gpt-5"\n')
    hermes = home / ".hermes" / "config.yaml"
    hermes.write_text("model:\n  default: x\n")

    changes, _ = apply(home)
    assert {c.agent for c in changes} == {"claude-code", "omp", "codex", "hermes"}
    assert {c.agent for c in changes if c.automatic} == {"claude-code", "omp"}

    installed = json.loads(settings.read_text())
    assert installed["model"] == "opus"
    assert set(installed["hooks"]) == set(integrations.CLAUDE_EVENTS)
    assert installed["hooks"]["Stop"][0]["hooks"][0]["command"] == "say done"  # theirs, kept first
    ours = installed["hooks"]["PostToolUseFailure"][0]
    assert ours["matcher"] == "Bash" and ours["hooks"][0]["async"] is True
    assert ours["hooks"][0]["command"] == "/opt/feed/bin/feed agent-hook claude"
    assert installed["hooks"]["SessionEnd"][0]["hooks"][0] == {
        "type": "command",
        "command": "/opt/feed/bin/feed agent-hook claude",
        "timeout": 10,
    }
    assert (home / ".claude" / "settings.json.before-feed-block").exists()

    extension = (home / ".omp" / "agent" / "extensions" / "feed-block.ts").read_text()
    assert integrations.MARKER in extension and '["/opt/feed/bin/feed"]' in extension
    assert '"omp"' in extension
    assert '[mcp_servers.feed-block]\ncommand = "/opt/feed/bin/feed"\nargs = ["--mcp"]' in codex.read_text()
    assert codex.read_text().startswith('model = "gpt-5"\n')
    assert "mcp_servers:\n  feed-block:\n" in hermes.read_text()

    assert apply(home)[0] == []  # a second install changes nothing

    apply(home, uninstall=True)
    assert json.loads(settings.read_text()) == theirs
    assert not (home / ".omp" / "agent" / "extensions" / "feed-block.ts").exists()
    assert codex.read_text() == 'model = "gpt-5"\n'
    assert hermes.read_text() == "model:\n  default: x\n"


def test_install_skips_what_it_cannot_change_safely(home):
    (home / ".claude" / "settings.json").write_text("{ not json")
    (home / ".hermes" / "config.yaml").write_text("mcp_servers:\n  other: {}\n")
    changes, notes = integrations.plan(home, COMMAND)
    assert {c.agent for c in changes} == {"omp", "codex"}
    assert any(note.startswith("claude-code:") and "not readable JSON" in note for note in notes)
    assert any(note.startswith("hermes:") and "add feed-block yourself" in note for note in notes)


def test_only_agents_that_are_installed_are_touched(tmp_path):
    assert integrations.plan(tmp_path, COMMAND) == ([], [])


def test_feed_install_asks_unless_told_yes(home, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(home))
    (home / ".hermes" / "config.yaml").write_text("model: x\n")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["--install"]) == 1
    assert "Run again with --yes" in capsys.readouterr().out
    assert not (home / ".claude" / "settings.json").exists()

    assert cli.main(["--install", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Posting automatically: claude-code, omp" in out
    assert "Posting through MCP: codex, hermes" in out
    assert cli.main(["--install", "--uninstall", "--yes"]) == 0
    assert "agent-hook" not in (home / ".claude" / "settings.json").read_text()


# -- the hook command itself


def test_agent_hook_posts_from_a_claude_payload_and_never_fails(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "a@b"], ["config", "user.name", "a"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "f").write_text("x\n")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "f"], cwd=repo, check=True)
    db = tmp_path / "feed.db"
    env = {"FEED_BLOCK_DB": str(db), "FEED_BLOCK_CONFIG": str(tmp_path / "none.toml")}

    def hook(payload: object) -> subprocess.CompletedProcess:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(
            [sys.executable, "-m", "feed_block.cli", "agent-hook", "claude"],
            input=text, capture_output=True, text=True, env={**os.environ, **env},
        )  # fmt: skip

    common = {"session_id": "s9", "cwd": str(repo)}
    assert hook({**common, "hook_event_name": "SessionStart"}).returncode == 0
    (repo / "f").write_text("x\ny\n")
    assert hook({**common, "hook_event_name": "Stop", "last_assistant_message": "Edited f."}).returncode == 0
    assert hook("not json at all").returncode == 0  # reported on stderr, never a failure
    with Store(db) as store:
        [post] = store.list()
        assert (post.agent, post.kind, post.title) == ("claude-code", "session", "Edited f.")
