"""Automatic posts: agent events in a real repository, and only results come out."""

from __future__ import annotations

import subprocess
import sys

import pytest

from feed_block import autopost
from feed_block.config import Config
from feed_block.store import Store


def git(*args: str, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "handover"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "agent@example.com", cwd=root)
    git("config", "user.name", "agent", cwd=root)
    (root / "a.txt").write_text("one\n")
    git("add", "a.txt", cwd=root)
    git("commit", "-qm", "start", cwd=root)
    return root


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "feed.db") as s:
        yield s


def send(store, repo, event, config=None, session="s1", agent="claude-code", **fields):
    payload = {"agent": agent, "session": session, "cwd": str(repo), "event": event, **fields}
    return autopost.handle(payload, store, config or Config())


def commit(repo, name, text, message):
    (repo / name).write_text(text)
    git("add", name, cwd=repo)
    git("commit", "-qm", message, cwd=repo)


def test_a_session_posts_one_summary_that_follows_its_work(repo, store):
    send(store, repo, "start")
    assert send(store, repo, "turn", message="Looked around.") == []  # nothing changed yet
    assert store.count() == 0

    commit(repo, "b.txt", "two\nthree\n", "add offline mode")
    send(store, repo, "command", command="uv run pytest -q", ok=False, output="=== 3 failed, 89 passed ===")
    assert store.count() == 0  # a test run is counted, not posted
    send(store, repo, "turn", message="## Summary\nAdded **offline mode**. Tests still need work.")
    [first] = store.list()
    assert first.kind == "session" and first.session == "s1"
    assert first.title == "Added offline mode."
    assert first.failed and first.body == "tests still failing · pytest · 3 failed · 1 commit"
    assert (first.repo, first.branch, first.additions, first.deletions) == ("handover", "main", 2, 0)

    send(store, repo, "command", command="pytest", ok=True, output="92 passed")
    send(store, repo, "turn", message="Fixed the tests.")
    [second] = store.list()  # replaced, not added
    assert second.id > first.id and not second.failed and second.title == "Fixed the tests."


def test_uncommitted_work_still_makes_a_summary(repo, store):
    send(store, repo, "start")
    (repo / "a.txt").write_text("one\nmore\n")
    send(store, repo, "end", message="")
    [post] = store.list()
    assert post.body == "uncommitted" and post.title == "worked on handover"


def test_two_sessions_post_separately(repo, store):
    send(store, repo, "start", session="s1", agent="omp")
    send(store, repo, "start", session="s2", agent="omp")
    commit(repo, "b.txt", "x\n", "one change")
    send(store, repo, "turn", session="s1", agent="omp", message="First.")
    send(store, repo, "turn", session="s2", agent="omp", message="Second.")
    assert sorted(p.session for p in store.list()) == ["s1", "s2"]


def test_a_pr_is_posted_once(repo, store):
    command = 'gh pr create --title "Offline mode" --body x'
    output = "https://github.com/o/handover/pull/42\n"
    assert send(store, repo, "command", command=command, ok=True, output=output)
    assert send(store, repo, "command", command=command, ok=True, output=output) == []
    [post] = store.list()
    assert (post.kind, post.title, post.link) == ("pr", "opened PR #42 Offline mode", output.strip())


def test_a_failed_pr_create_posts_nothing(repo, store):
    assert send(store, repo, "command", command="gh pr create -t x", ok=False, output="error") == []


def test_a_merge_is_posted_with_its_title(repo, store):
    output = "✓ Squashed and merged pull request o/handover#42 (Offline mode)\n"
    send(store, repo, "command", command="gh pr merge 42 --squash", ok=True, output=output)
    [post] = store.list()
    assert (post.kind, post.title) == ("merge", "merged PR #42 Offline mode")


def test_deploys_post_whether_they_work_or_not(repo, store):
    send(store, repo, "command", command="fly deploy --remote-only", ok=True, output="done")
    send(store, repo, "command", command="cd . && npx vercel --prod", ok=False, output="x\nError: no token\n")
    failed, shipped = store.list()
    assert (shipped.title, shipped.failed) == ("deployed handover with fly", False)
    assert (failed.title, failed.failed, failed.body) == (
        "deploy failed: vercel --prod",
        True,
        "Error: no token",
    )


def test_switches_turn_posts_off(repo, store):
    config = Config(posts={"session": False, "pr": True, "merge": True, "deploy": False})
    send(store, repo, "start", config=config)
    commit(repo, "b.txt", "x\n", "change")
    send(store, repo, "turn", config=config, message="Done.")
    send(store, repo, "command", config=config, command="fly deploy", ok=True)
    assert store.count() == 0


def test_events_without_a_session_or_outside_git_are_harmless(tmp_path, store):
    assert autopost.handle({"agent": "x", "event": "turn"}, store, Config()) == []
    assert send(store, tmp_path, "start") == []
    assert send(store, tmp_path, "turn", message="hi") == []


@pytest.mark.parametrize(
    ("command", "found"),
    [
        ("pytest -q", "pytest"),
        ("cd api && uv run pytest tests/", "pytest"),
        ("FOO=1 npx vitest run", "vitest"),
        (".venv/bin/pytest -x", "pytest"),
        ("python -m pytest", "python -m pytest"),
        ("npm test -- --watch=false", "npm test"),
        ("echo pytest", None),
        ("git commit -m 'run pytest'", None),
        ("npm install", None),
    ],
)
def test_test_commands_are_recognized(command, found):
    assert autopost.matches(command, Config().test_commands) == found


@pytest.mark.parametrize(
    ("message", "title"),
    [
        ("Done.", "Done."),
        ("# Summary\n\nI fixed the bug. Then more.", "I fixed the bug."),
        ("## Only a heading", "Only a heading"),
        ("- Added [the feed](https://x.test) to `cli.py`: works", "Added the feed to cli.py: works"),
        ("", ""),
        ("x" * 150, "x" * 99 + "…"),
    ],
)
def test_headline(message, title):
    assert autopost.headline(message) == title


def test_simultaneous_events_leave_one_summary(repo, store, tmp_path):
    """A reply ending as the session closes: separate processes, one summary."""
    send(store, repo, "start")
    commit(repo, "b.txt", "x\n", "change")
    script = (
        "import sys; from feed_block import autopost; from feed_block.config import Config; "
        "from feed_block.store import Store; "
        "s = Store(sys.argv[1]); "
        "autopost.handle({'agent': 'claude-code', 'session': 's1', 'cwd': sys.argv[2], "
        "'event': 'turn', 'message': 'Done.'}, s, Config())"
    )
    runs = [subprocess.Popen([sys.executable, "-c", script, str(store.path), str(repo)]) for _ in range(8)]
    assert all(run.wait(timeout=60) == 0 for run in runs)
    assert [p.kind for p in store.list()] == ["session"]
