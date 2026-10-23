"""Automatic posts: agent events in a real repository, and only results come out."""

from __future__ import annotations

import os
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
        ("Want me to also update the docs?", ""),
        ("Shipped the fix. Want me to update the docs?", "Shipped the fix."),
        ("Should I merge it?\n\nThe tests pass on main.", "The tests pass on main."),
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


def test_piped_failing_tests_still_count_as_failing(repo, store):
    """`pytest | tail -5` exits 0: the runner's own count decides."""
    send(store, repo, "start")
    commit(repo, "b.txt", "x\n", "change")
    send(
        store,
        repo,
        "command",
        command="uv run pytest -q 2>&1 | tail -3",
        ok=True,
        output="== 3 failed, 40 passed ==",
    )
    send(store, repo, "turn", message="Wired it up.")
    [post] = store.list()
    assert post.failed and "3 failed" in post.body
    send(store, repo, "command", command="pytest | tail -1", ok=True, output="== 0 failed, 43 passed ==")
    send(store, repo, "turn", message="Fixed.")
    assert not store.list()[0].failed


def test_the_session_ending_keeps_the_last_replys_title(repo, store):
    send(store, repo, "start")
    commit(repo, "b.txt", "x\n", "wip")
    send(store, repo, "turn", message="Wired offline mode into the sync worker.")
    send(store, repo, "end")
    assert [p.title for p in store.list()] == ["Wired offline mode into the sync worker."]


def test_a_reply_without_new_work_does_not_bump_the_summary(repo, store):
    send(store, repo, "start")
    commit(repo, "b.txt", "x\n", "change")
    send(store, repo, "turn", message="Added the change.")
    [first] = store.list()
    send(store, repo, "command", command="fly deploy", ok=True, output="ok")  # something else posts
    assert send(store, repo, "turn", message="It works because the queue retries.") == []
    assert send(store, repo, "end") == []
    summary = [p for p in store.list() if p.kind == "session"]
    assert [(p.id, p.title) for p in summary] == [(first.id, "Added the change.")]  # same post, not bumped
    commit(repo, "c.txt", "y\n", "more")
    assert send(store, repo, "turn", message="Added more.")  # new work: a new summary on top
    assert store.list()[0].title == "Added more."


@pytest.mark.parametrize(
    ("command", "title"),
    [
        (
            """gh pr create --title "Switch models" --body "$(cat <<'EOF'\nHermes's picker\nEOF\n)\"""",
            "Switch models",
        ),
        ("gh pr create -t 'Resume a session' --body x", "Resume a session"),
        ("gh pr create --title=Fix", "Fix"),
        ('gh pr create --title "Say \\"hi\\""', 'Say "hi"'),
        ('gh pr create --title "$(git log -1 --format=%s)"', None),
        ("gh pr create --fill", None),
    ],
)
def test_pr_titles_survive_heredoc_bodies(command, title):
    assert autopost.pr_title(command) == title


def test_pulled_commits_are_not_the_sessions_work(repo, store, tmp_path):
    """A fast-forward pull brings others' commits into `base..HEAD`; only the session's own count."""
    clone = tmp_path / "clone"
    git("clone", "-q", str(repo), str(clone), cwd=tmp_path)
    git("config", "user.email", "agent@example.com", cwd=clone)
    git("config", "user.name", "agent", cwd=clone)
    send(store, clone, "start")
    for n in range(3):
        commit(repo, f"up{n}.txt", "x\n" * 50, f"upstream {n}")
    git("pull", "-q", "--ff-only", cwd=clone)
    commit(clone, "mine.txt", "one\ntwo\n", "my change")
    send(store, clone, "turn", message="Made my change.")
    [post] = store.list()
    assert post.body == "1 commit"
    assert (post.additions, post.deletions) == (2, 0)


def test_merging_main_in_counts_only_the_sessions_commits(repo, store):
    git("checkout", "-qb", "feature", cwd=repo)
    send(store, repo, "start")
    commit(repo, "mine.txt", "one\n", "my change")
    git("checkout", "-q", "main", cwd=repo)
    # Made on main before the session, by its date; the reflog's dates are not in order then.
    (repo / "main.txt").write_text("m\n" * 40)
    git("add", "main.txt", cwd=repo)
    subprocess.run(
        ["git", "commit", "-qm", "main work"],
        cwd=repo, check=True, env={**os.environ, "GIT_COMMITTER_DATE": "2000-01-01T00:00:00"},
    )  # fmt: skip
    git("checkout", "-q", "feature", cwd=repo)
    git("merge", "-q", "--no-edit", "main", cwd=repo)
    send(store, repo, "turn", message="Synced with main.")
    [post] = store.list()
    assert post.body == "1 commit" and (post.additions, post.deletions) == (1, 0)


def test_new_untracked_files_are_work(repo, store):
    send(store, repo, "start")
    (repo / "new.py").write_text("a\nb\nc")  # no trailing newline: still three lines
    (repo / "blob.bin").write_bytes(b"\0\1\2\n")  # binary: not counted
    send(store, repo, "end")
    [post] = store.list()
    assert post.body == "uncommitted" and (post.additions, post.deletions) == (3, 0)


@pytest.mark.parametrize(
    ("output", "ok", "summary"),
    [
        ("Test Suites: 1 failed, 2 passed\nTests:       4 failed, 30 passed", False, "jest · 4 failed"),
        ("error: 3 failed to compile\nTests: 12 passed", True, "jest · passing"),
        ("collected 0 items / 1 error\n== 0 failed ==", False, "jest · failing"),
        ("== 2 failed ==\n...\n== 0 failed, 9 passed ==", True, "jest · passing"),
    ],
)
def test_the_runners_final_count_decides(repo, store, output, ok, summary):
    send(store, repo, "start")
    send(store, repo, "command", command="npx jest", ok=ok, output=output)
    state = store.session_state("s1", str(repo))
    assert state["tests_summary"] == summary


@pytest.mark.parametrize(
    ("command", "found"),
    [
        ('git commit -m "fix; fly deploy --remote-only"', None),
        ("echo 'a && fly deploy now'", None),
        ('git commit -m "say \\"hi\\"; fly deploy now"', None),
        ("cd api && fly deploy", "fly deploy"),
        ("cat <<EOF > notes\nHermes's notes\nEOF\nfly deploy", "fly deploy"),  # quotes never close
    ],
)
def test_separators_inside_quotes_do_not_split(command, found):
    assert autopost.matches(command, Config().deploy_commands) == found
