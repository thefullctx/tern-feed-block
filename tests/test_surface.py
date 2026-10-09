"""The surface: clipboard, share, and a live pane that never restarts."""

from __future__ import annotations

import json
import subprocess
import threading
import time

import pytest
from tern_sdk import session as session_mod

from feed_block import surface as surface_mod
from feed_block.surface import Feed
from feed_block.store import Store
from terminal import Term


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "feed.db") as s:
        yield s


def post(store, **kw):
    kw.setdefault("agent", "hermes")
    kw.setdefault("kind", "pr")
    kw.setdefault("title", "pushed PR #42 to handover")
    kw.setdefault("link", "https://github.com/x/handover/pull/42")
    return store.post(**kw)


# ---------------------------------------------------------------------------
# actions


def test_clipboard_uses_the_first_tool_installed(monkeypatch):
    calls = []

    def fake_run(command, **kw):
        calls.append((command, kw.get("input")))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        surface_mod.shutil, "which", lambda name: "/usr/bin/" + name if name == "pbcopy" else None
    )
    monkeypatch.setattr(surface_mod.subprocess, "run", fake_run)
    assert surface_mod.clipboard("hello feed") is True
    assert calls == [(["pbcopy"], "hello feed")]


def test_clipboard_without_any_tool(monkeypatch):
    monkeypatch.setattr(surface_mod.shutil, "which", lambda name: None)
    assert surface_mod.clipboard("hello") is False


def test_clipboard_falls_past_a_failing_tool(monkeypatch):
    def fake_run(command, **kw):
        if command[0] == "pbcopy":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(surface_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(surface_mod.subprocess, "run", fake_run)
    assert surface_mod.clipboard("hello") is True  # wl-copy answered


def test_open_url_reports_failure(monkeypatch):
    monkeypatch.setattr(surface_mod.webbrowser, "open", lambda url: True)
    assert surface_mod.open_url("https://x.test") is True
    monkeypatch.setattr(surface_mod.webbrowser, "open", lambda url: (_ for _ in ()).throw(RuntimeError("no")))
    assert surface_mod.open_url("https://x.test") is False


def test_share_opens_x_with_the_prefilled_post(store, monkeypatch):
    opened = []
    monkeypatch.setattr(surface_mod, "open_url", lambda url: opened.append(url) or True)
    made = post(store)
    feed = Feed(None, store)
    feed.share_post(made)
    assert opened == [
        "https://x.com/intent/tweet?text=pushed%20PR%20%2342%20to%20handover"
        "%0Ahttps%3A%2F%2Fgithub.com%2Fx%2Fhandover%2Fpull%2F42"
    ]
    assert feed.toast == ("share opened on x.com", "post it from there")
    assert feed.dirty


def test_copy_puts_the_summary_on_the_clipboard(store, monkeypatch):
    copied = []
    monkeypatch.setattr(surface_mod, "clipboard", lambda text: copied.append(text) or True)
    feed = Feed(None, store)
    feed.copy_post(post(store))
    assert copied == ["pushed PR #42 to handover\nhttps://github.com/x/handover/pull/42"]
    assert feed.toast[0] == "copied to the clipboard"


def test_copy_without_a_clipboard_tool_says_so(store, monkeypatch):
    monkeypatch.setattr(surface_mod, "clipboard", lambda text: False)
    feed = Feed(None, store)
    feed.copy_post(post(store))
    assert feed.toast[0] == "no clipboard tool"


def test_open_action_opens_the_link(store, monkeypatch):
    opened = []
    monkeypatch.setattr(surface_mod, "open_url", lambda url: opened.append(url) or True)
    feed = Feed(None, store)
    feed.open_post(post(store))
    assert opened == ["https://github.com/x/handover/pull/42"]
    feed.open_post(post(store, link=None))
    assert feed.toast == ("nothing to open", "this post has no link")


def test_open_refuses_links_that_are_not_web_pages(store, monkeypatch):
    opened = []
    monkeypatch.setattr(surface_mod, "open_url", lambda url: opened.append(url) or True)
    feed = Feed(None, store)
    for link in ("file:///etc/passwd", "vscode://open?x", "javascript:alert(1)"):
        feed.open_post(post(store, link=link))
        assert feed.toast[0] == "not a web link"
    assert opened == []


def test_toggle_filters(store):
    feed = Feed(None, store)
    feed.toggle("hermes")
    assert feed.selected == {"hermes"}
    feed.toggle("hermes")
    assert feed.selected == set()


# ---------------------------------------------------------------------------
# the live pane


def _wait_for(term: Term, needle: str, deadline: float) -> bool:
    """Reads the scripted terminal, acking frames (credits), until `needle` shows in one."""
    while time.monotonic() < deadline:
        for message in term.messages():
            if message.verb == "f":
                term.ack("s1", int(message.body["s"]))
                if needle in json.dumps(message.body):
                    return True
        time.sleep(0.02)
    return False


def test_a_new_post_lands_live_without_a_restart(tmp_path):
    """The done criterion on the pane: a post from another process shows up live."""
    db = tmp_path / "feed.db"
    with Store(db) as seeder:
        seeder.post(agent="hermes", kind="pr", title="the first post", link="https://x.test")
    term = Term()
    worker: list = []
    try:
        term.reply()
        term.da1()
        session = session_mod.start(term.in_r, term.out_w, app="feed-block", timeout=1.0)
        assert session is not None
        term.read()

        # The pane owns its store, exactly as `feed` does in the main thread.
        def run_pane() -> None:
            with Store(db) as store:
                feed = Feed(session, store)
                worker.append(feed)
                feed.run()

        thread = threading.Thread(target=run_pane, daemon=True)
        thread.start()

        # The pane opens on the first post, then shows a post from another process: live.
        deadline = time.monotonic() + 5
        assert _wait_for(term, "the first post", deadline), "the pane never showed the first post"
        with Store(db) as writer:
            writer.post(agent="codex", kind="test", title="a post that lands live", status="ok")
        assert _wait_for(term, "a post that lands live", deadline), "the new post never reached the pane"
        feed = worker[0] if worker else None
        assert feed is not None and thread.is_alive() and not feed.exit  # still the same session

        term.key("q")  # the pane leaves on q
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert feed.exit
    finally:
        term.close()
