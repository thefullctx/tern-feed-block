"""CLI behavior: post, list, doctor, and the git hooks end to end."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from feed_block import cli
from feed_block.store import Store


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "feed.db"
    monkeypatch.setenv("FEED_BLOCK_DB", str(path))
    return path


def run(*args: str) -> tuple[int, str, str]:
    done = subprocess.run(
        [sys.executable, "-m", "feed_block.cli", *args],
        capture_output=True,
        text=True,
        cwd=os.getcwd(),
        stdin=subprocess.DEVNULL,
        env={**os.environ, "FEED_BLOCK_DB": os.environ["FEED_BLOCK_DB"]},
        timeout=60,
    )
    return done.returncode, done.stdout, done.stderr


def test_post_and_list_round_trip(db, capsys):
    assert (
        cli.main(
            [
                "post",
                "--agent",
                "hermes",
                "--kind",
                "pr",
                "--title",
                "pushed PR #42 to handover",
                "--repo",
                "handover",
                "--add",
                "120",
                "--del",
                "34",
                "--link",
                "https://github.com/x/handover/pull/42",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "posted #1 to feed-block in handover · hermes · pr · +120 −34 · ok" in out

    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "@hermes" in out and "pr" in out and "pushed PR #42 to handover" in out
    assert "today" in out


def test_post_falls_back_to_the_agent_env(db, monkeypatch, capsys):
    monkeypatch.setenv("FEED_BLOCK_AGENT", "codex")
    assert cli.main(["post", "--kind", "test", "--title", "42 passing", "--status", "failed"]) == 0
    with Store(db) as store:
        post = store.list()[0]
        assert post.agent == "codex" and post.failed


def test_post_without_an_agent_fails(db, monkeypatch, capsys):
    monkeypatch.delenv("FEED_BLOCK_AGENT", raising=False)
    assert cli.main(["post", "--kind", "note", "--title", "nameless"]) == 1
    assert "agent is required" in capsys.readouterr().err


def test_list_json_is_the_store(db, capsys):
    cli.main(["post", "--agent", "omp", "--kind", "deploy", "--title", "shipped 0.1", "--add", "3"])
    capsys.readouterr()
    assert cli.main(["list", "--json", "--limit", "5"]) == 0
    posts = json.loads(capsys.readouterr().out)
    assert posts[0]["agent"] == "omp"
    assert posts[0]["kind"] == "deploy"
    assert posts[0]["additions"] == 3
    assert set(posts[0]) == {
        "id",
        "agent",
        "kind",
        "title",
        "body",
        "repo",
        "link",
        "additions",
        "deletions",
        "status",
        "created_at",
    }


def test_list_filters_by_agent_and_says_so(db, capsys):
    cli.main(["post", "--agent", "hermes", "--kind", "note", "--title", "hers"])
    cli.main(["post", "--agent", "codex", "--kind", "note", "--title", "theirs"])
    capsys.readouterr()
    assert cli.main(["list", "--agent", "codex"]) == 0
    out = capsys.readouterr().out
    assert "theirs" in out and "hers" not in out
    assert cli.main(["list", "--agent", "nobody"]) == 0
    assert "no posts from nobody" in capsys.readouterr().out


def test_empty_feed_list_says_how_to_post(db, capsys):
    assert cli.main(["list"]) == 0
    assert "feed post --agent you" in capsys.readouterr().out


def test_bad_kind_is_an_argparse_error(db):
    with pytest.raises(SystemExit):
        cli.main(["post", "--agent", "x", "--kind", "tweet", "--title", "no"])


def test_version(db, capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("feed ")


def test_doctor_reports_the_feed(db, capsys):
    cli.main(["post", "--agent", "hermes", "--kind", "note", "--title", "hello"])
    capsys.readouterr()
    assert cli.main(["--doctor"]) == 0
    out = capsys.readouterr().out
    assert "python 3.1" in out
    assert str(db) in out and "1 posts" in out


def test_doctor_fails_when_the_feed_cannot_open(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FEED_BLOCK_DB", str(tmp_path))  # a directory: sqlite cannot open it
    assert cli.main(["--doctor"]) == 1
    assert capsys.readouterr().out.startswith(("ok   ", "MISS "))


# ---------------------------------------------------------------------------
# hooks


def git(*args: str, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "handover"
    root.mkdir()
    git("init", "-q", cwd=root)
    git("config", "user.email", "agent@example.com", cwd=root)
    git("config", "user.name", "hermes", cwd=root)
    (root / "a.txt").write_text("one\n")
    git("add", "a.txt", cwd=root)
    git("commit", "-qm", "first post for the feed\n\na body line", cwd=root)
    return root


def test_hook_install_and_uninstall_are_reversible(repo, db, monkeypatch):
    monkeypatch.chdir(repo)
    # A hook that was already there is kept beside feed-block's, and restored after.
    other = repo / ".git" / "hooks" / "post-merge"
    other.write_text("#!/bin/sh\necho mine\n")

    assert cli.main(["hook", "install", "--repo", str(repo)]) == 0
    for name in ("post-commit", "post-merge"):
        hook = repo / ".git" / "hooks" / name
        assert hook.is_file() and os.access(hook, os.X_OK)
        assert cli.HOOK_MARKER in hook.read_text()
        body = hook.read_text()
        assert "@FEED_BIN@" not in body and "@PYTHON@" not in body  # this install's command, baked in
        assert sys.executable in body
    backup = repo / ".git" / "hooks" / "post-merge.before-feed-block"
    assert backup.read_text() == "#!/bin/sh\necho mine\n"

    assert cli.main(["hook", "install", "--repo", str(repo), "--uninstall"]) == 0
    assert other.read_text() == "#!/bin/sh\necho mine\n"
    assert not backup.exists()
    assert not (repo / ".git" / "hooks" / "post-commit").exists()


def test_an_installed_hook_posts_for_real(repo, db, monkeypatch):
    """The hook itself runs: a commit posts, with the binary this install baked in."""
    monkeypatch.chdir(repo)
    cli.main(["hook", "install", "--repo", str(repo)])
    (repo / "a.txt").write_text("one\ntwo\n")
    git("commit", "-qam", "the hook's own commit", cwd=repo)  # post-commit fires
    with Store(db) as store:
        posts = [p for p in store.list() if p.title == "the hook's own commit"]
        assert posts and posts[0].kind == "commit"


def test_hook_posts_a_commit_with_stats(repo, db, capsys, monkeypatch):
    monkeypatch.chdir(repo)
    assert cli.main(["hook", "install", "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert cli.main(["hook", "run", "--name", "post-commit", "--"]) == 0
    out = capsys.readouterr().out
    assert "posted #1" in out and "commit" in out
    with Store(db) as store:
        post = store.list()[0]
        assert post.kind == "commit"
        assert post.title == "first post for the feed"
        assert post.body == "a body line"
        assert post.repo == "handover"
        assert post.agent == "hermes"
        assert post.additions == 1 and post.deletions == 0  # one added line


def test_hook_posts_a_merge(repo, db, capsys, monkeypatch):
    monkeypatch.chdir(repo)
    git("checkout", "-qb", "side", cwd=repo)
    (repo / "b.txt").write_text("two\n")
    git("add", "b.txt", cwd=repo)
    git("commit", "-qm", "side work", cwd=repo)
    git("checkout", "-q", "-", cwd=repo)
    git("merge", "--no-ff", "-qm", "release the side work", "side", cwd=repo)

    assert cli.main(["hook", "run", "--name", "post-merge", "--", "0"]) == 0
    capsys.readouterr()
    with Store(db) as store:
        post = store.list()[0]
        assert post.kind == "merge"
        assert post.title.startswith("merged")


def test_hook_outside_a_repository_posts_nothing(tmp_path, db, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["hook", "run", "--name", "post-commit", "--"]) == 0
    with Store(db) as store:
        assert store.count() == 0


def test_hook_skips_a_commit_already_posted(repo, db, capsys, monkeypatch):
    monkeypatch.chdir(repo)
    git("remote", "add", "origin", "git@github.com:thefullctx/handover.git", cwd=repo)
    assert cli.main(["hook", "run", "--name", "post-commit", "--"]) == 0
    capsys.readouterr()
    with Store(db) as store:
        first = store.list()[0]
        assert first.link == f"https://github.com/thefullctx/handover/commit/{sha(repo)}"
    # The same commit, replayed (post-rewrite, a second install): no second post.
    assert cli.main(["hook", "run", "--name", "post-commit", "--"]) == 0
    assert capsys.readouterr().out == ""
    with Store(db) as store:
        assert store.count() == 1


def test_hooks_install_into_a_worktree(repo, db, tmp_path, monkeypatch):
    """A worktree's .git is a file: the hooks go where git runs them, the common hooks dir."""
    tree = tmp_path / "wt"
    git("worktree", "add", "-q", str(tree), cwd=repo)
    monkeypatch.chdir(tree)
    assert cli.main(["hook", "install", "--repo", str(tree)]) == 0
    assert cli.HOOK_MARKER in (repo / ".git" / "hooks" / "post-commit").read_text()


def test_hooks_install_honors_core_hooks_path(repo, db, monkeypatch):
    monkeypatch.chdir(repo)
    git("config", "core.hooksPath", ".githooks", cwd=repo)
    assert cli.main(["hook", "install", "--repo", str(repo)]) == 0
    assert cli.HOOK_MARKER in (repo / ".githooks" / "post-commit").read_text()
    assert not (repo / ".git" / "hooks" / "post-commit").exists()


def test_hooks_call_this_installs_feed(monkeypatch):
    """Not another `feed` on PATH: the hook runs the code that installed it."""
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/elsewhere/bin/feed")
    scripts = cli.sysconfig.get_path("scripts")
    assert cli.feed_bin() == os.path.join(scripts, "feed")


def test_an_amended_commit_is_not_posted_twice(repo, db, monkeypatch):
    monkeypatch.chdir(repo)
    cli.main(["hook", "install", "--repo", str(repo)])
    (repo / "a.txt").write_text("one\ntwo\n")
    git("commit", "-qam", "posted once", cwd=repo)
    git("commit", "-q", "--amend", "-m", "posted once, reworded", cwd=repo)
    with Store(db) as store:
        assert [p.title for p in store.list()] == ["posted once"]


@pytest.mark.parametrize(
    ("remote", "link"),
    [
        ("git@github.com:o/r.git", "https://github.com/o/r/commit/abc"),
        ("https://user@github.com/o/r.git", "https://github.com/o/r/commit/abc"),
        ("ssh://git@github.com:22/o/r.git", "https://github.com/o/r/commit/abc"),
        ("git@gitlab.com:g/sub/r.git", "https://gitlab.com/g/sub/r/-/commit/abc"),
        ("https://bitbucket.org/o/r", "https://bitbucket.org/o/r/commits/abc"),
        ("https://git.example.com/o/r.git", None),
        (None, None),
    ],
)
def test_commit_url_from_remotes(remote, link):
    assert cli.commit_url(remote, "abc") == link


def test_a_broken_feed_file_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    path = tmp_path / "feed.db"
    path.write_bytes(b"not a database, just bytes" * 100)
    monkeypatch.setenv("FEED_BLOCK_DB", str(path))
    assert cli.main(["list"]) == 1
    assert capsys.readouterr().err.startswith("feed: ")


def sha(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_hook_install_prints_the_agent_instruction(repo, db, capsys):
    assert cli.main(["--install", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "call the feed-block MCP tool `post`" in out
    assert "claude mcp add feed-block" in out
    assert json.loads(out[out.index('{\n  "mcpServers"') :])["mcpServers"]["feed-block"]["args"][-2:] == [
        "feed",
        "--mcp",
    ]


def test_hook_install_requires_a_repository(tmp_path, db, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["hook", "install"]) == 2
    assert "not a git repository" in capsys.readouterr().err


def test_mcp_flag_runs_the_server(db):
    code, out, err = run("--mcp")
    assert code == 0  # stdin was empty: the server exits cleanly
    assert out == ""


def test_bare_feed_outside_tern_prints_plain_text(db, capsys):
    cli.main(["post", "--agent", "hermes", "--kind", "note", "--title", "hello rail"])
    capsys.readouterr()
    # TSP is not available in tests (no tty handshake): the plain feed prints.
    assert cli.main([]) == 0
    assert "hello rail" in capsys.readouterr().out
