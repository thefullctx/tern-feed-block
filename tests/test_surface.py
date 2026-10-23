"""The surface: clipboard, share, and a live pane that never restarts."""

from __future__ import annotations

import json
import subprocess
import threading
import time

import pytest
from tern_sdk import Key
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


# ---------------------------------------------------------------------------
# the keyboard


class FakeSurface:
    """Records what the pane would draw and where it would scroll."""

    def __init__(self) -> None:
        self.page: dict = {}
        self.revealed: list[tuple[str, str]] = []

    def render(self, **page) -> None:
        self.page = page

    def reveal(self, id: str, at: str = "nearest") -> None:
        self.revealed.append((id, at))

    def stylesheet(self, name: str, css: str | None) -> None:
        pass


def keyed(store) -> Feed:
    feed = Feed(None, store)
    feed.surface = FakeSurface()
    feed.render()
    return feed


def press(feed: Feed, *names: str, **mods) -> None:
    for name in names:
        feed.input(Key(name, **mods))
        if feed.dirty:
            feed.render()
            feed.dirty = False


def test_j_and_k_move_the_cursor_and_scroll_to_it(store):
    first, second, third = (post(store, title=f"post {n}") for n in range(3))
    feed = keyed(store)
    press(feed, "j")
    assert feed.cursor == third.id  # the first move lands on the top post
    assert feed.surface.revealed[-1] == ("main.feed.strip", "start")
    press(feed, "j", "down")
    assert feed.cursor == first.id
    assert feed.surface.revealed[-1] == (f"main.feed.p{first.id}", "nearest")
    press(feed, "j")
    assert feed.cursor == first.id  # the bottom holds
    press(feed, "k")
    assert feed.cursor == second.id
    press(feed, "g", shift=True)
    assert feed.cursor == first.id
    press(feed, "g")
    assert feed.cursor == third.id


def test_enter_c_and_s_act_on_the_cursors_post(store, monkeypatch):
    opened, copied = [], []
    monkeypatch.setattr(surface_mod, "open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(surface_mod, "clipboard", lambda text: copied.append(text) or True)
    post(store, title="older", link="https://x.test/older")
    post(store, title="newer", link="https://x.test/newer")
    feed = keyed(store)
    press(feed, "enter")
    assert feed.toast == ("no post selected", "j or k picks one") and opened == []
    press(feed, "j", "j", "enter", "c", "s")
    assert opened[0] == "https://x.test/older" and opened[1].startswith("https://x.com/intent/tweet")
    assert copied == ["older\nhttps://x.test/older"]


def test_number_keys_t_and_x_filter_and_0_clears(store):
    post(store, agent="hermes", kind="pr")
    post(store, agent="codex", kind="deploy", status="failed")
    post(store, agent="codex", kind="session", title="summary")
    feed = keyed(store)
    assert feed.agents == ["codex", "hermes"]
    press(feed, "2")
    assert feed.selected == {"hermes"} and [p.agent for p in feed.posts] == ["hermes"]
    press(feed, "0")
    assert not feed.filtering
    press(feed, "t")
    assert feed.kind == "session"  # results first
    press(feed, "t")
    assert feed.kind == "pr"
    press(feed, "t", shift=True)
    assert feed.kind == "session"
    press(feed, "0", "x")
    assert feed.failed_only and [p.kind for p in feed.posts] == ["deploy"]


def test_t_cycles_back_to_every_kind(store):
    post(store, kind="pr")
    feed = keyed(store)
    press(feed, "t")
    assert feed.kind == "pr"
    press(feed, "t")
    assert feed.kind is None


def test_the_cursor_follows_its_post_through_a_filter(store):
    keep = post(store, agent="hermes", title="kept")
    post(store, agent="codex", title="filtered out")
    feed = keyed(store)
    press(feed, "j")  # codex's post, on top
    press(feed, "1")  # codex, the newest, is the first handle
    assert [p.agent for p in feed.posts] == ["codex"]
    press(feed, "0", "j")
    assert feed.cursor == keep.id
    press(feed, "1")  # codex only: the cursor's post is gone, so it moves to what is shown
    assert feed.cursor == feed.posts[0].id


def test_escape_steps_back_before_it_leaves(store):
    post(store, status="failed")
    feed = keyed(store)
    press(feed, "?", "x", "j")
    assert feed.keys and feed.failed_only and feed.cursor is not None
    press(feed, "escape")
    assert not feed.keys and not feed.exit
    press(feed, "escape")
    assert not feed.filtering and feed.cursor is not None
    press(feed, "escape")
    assert feed.cursor is None and not feed.exit
    press(feed, "escape")
    assert feed.exit


def test_q_and_ctrl_c_leave_at_once(store):
    feed = keyed(store)
    press(feed, "x", "q")
    assert feed.exit
    feed = keyed(store)
    press(feed, "c", ctrl=True)
    assert feed.exit


def test_keys_reach_the_live_pane(tmp_path):
    """Over a real session: j marks a post, ? opens the legend, q leaves."""
    db = tmp_path / "feed.db"
    with Store(db) as seeder:
        seeder.post(agent="hermes", kind="pr", title="the first post")
    term = Term()
    try:
        term.reply()
        term.da1()
        session = session_mod.start(term.in_r, term.out_w, app="feed-block", timeout=1.0)
        assert session is not None
        term.read()
        done: list = []

        def run_pane() -> None:
            with Store(db) as store:
                feed = Feed(session, store)
                done.append(feed)
                feed.run()

        thread = threading.Thread(target=run_pane, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        assert _wait_for(term, "the first post", deadline)
        term.key("j")
        assert _wait_for(term, "fb-cursor", deadline), "the cursor never reached the pane"
        term.key("?")
        assert _wait_for(term, "enter open", deadline), "the key legend never opened"
        term.key("q")
        thread.join(timeout=5)
        assert not thread.is_alive() and done[0].exit
    finally:
        term.close()
