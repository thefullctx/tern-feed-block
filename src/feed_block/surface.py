"""The Tern surface: the feed as a live pane, without a restart.

A store watcher (a query for the newest id, four times a second) is the whole
live story: writers are other processes, so polling the local file is both
simpler and safer than any push mechanism. Input never blocks on it.

The keyboard drives the same pane the mouse does: a cursor moves over the
posts (j/k, g/G) and acts on its post (enter, c, s); number keys, t and x
filter, 0 clears; `?` lists the keys. Esc steps back one thing at a time
(the legend, the filters, the cursor) before it leaves.
"""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import time
import webbrowser
from urllib.parse import urlsplit

from tern_sdk import Key, ui

from . import design, labels, views
from .store import Post, Store
from .views import Handlers

# Seconds between checks for new posts.
POLL = 0.25
# Seconds between re-renders that only refresh relative times.
TICK = 30.0
# How long the live dot pulses once a post lands.
PULSE = 2.5
# How long a toast stays up.
TOAST = 2.5
# The frame cap: renders are batched at roughly this rate.
FRAME = 0.05

# The order `t` steps through kinds: results first, then the rest.
KIND_ORDER = ("session", "pr", "merge", "deploy", "commit", "test", "note")

# Copy tools, in the order a machine is likely to have them.
CLIPBOARDS = (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"])


def clipboard(text: str) -> bool:
    """Puts text on the system clipboard; False when no tool is installed."""
    for command in CLIPBOARDS:
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.run(
                command,
                input=text,
                text=True,
                timeout=5,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            continue
    return False


def openable(url: str | None) -> bool:
    """Whether a post's link is a web page: agents write links, so no file: or app schemes."""
    return bool(url) and urlsplit(url).scheme.lower() in ("http", "https")


def open_url(url: str) -> bool:
    """Opens a URL in the system browser."""
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


class Feed:
    """One live pane over the local feed."""

    def __init__(self, session, store: Store):
        self.session = session
        self.store = store
        self.surface = None
        self.selected: set[str] = set()
        self.exit = False
        self.dirty = False
        self.latest = 0
        self.live_until = 0.0
        self.toast: tuple[str, str] | None = None
        self.toast_until = 0.0
        self.kind: str | None = None
        self.failed_only = False
        self.keys = False  # the key legend
        self.cursor: int | None = None  # the keyboard's post, by id
        self.posts: list[Post] = []  # the posts shown, as last rendered
        self.agents: list[str] = []  # the strip's handles, as last rendered
        self._reveal: tuple[str, str] | None = None  # a node to scroll to after the next render
        self.handlers = Handlers(
            open=self.open_post, copy=self.copy_post, share=self.share_post, toggle=self.toggle,
            kind=self.toggle_kind, failed=self.toggle_failed, keys=self.toggle_keys,
        )  # fmt: skip
        self._styled: tuple[str, ...] | None = None

    # -- the loop

    def run(self) -> int:
        with (
            self.session,
            self.session.open(mode="inline", title="feed-block", role="feed.session") as surface,
        ):
            self.surface = surface
            surface.stylesheet("feed-block", design.stylesheet())
            if self.session.caps.has("program-palette"):
                surface.palette(
                    dark=design.DARK, light=design.LIGHT, name={"dark": "feed violet", "light": "feed iris"}
                )
            self.latest = self.store.latest_id()
            self.render()
            surface.reveal(
                "main.feed.strip", "start"
            )  # the reader pins to the bottom; the feed reads from the top
            checked = rendered = time.monotonic()
            while not self.exit and not self.session.closed and not surface.closed:
                item = self.session.poll(0.05)
                if item is not None:
                    self.input(item)
                now = time.monotonic()
                if now - checked >= POLL:
                    checked = now
                    latest = self.store.latest_id()
                    if latest != self.latest:
                        self.latest = latest
                        self.live_until = now + PULSE  # the dot pulses when a post lands
                        self.dirty = True
                    elif now - rendered >= TICK:
                        self.dirty = True  # "2m" becomes "3m" on its own
                if self.toast is not None and now >= self.toast_until:
                    self.toast = None
                    self.dirty = True
                if self.dirty and now - rendered >= FRAME:
                    self.render()
                    self.dirty = False
                    rendered = now
        return 0

    def input(self, item) -> None:
        """Keys move, act, filter and leave; clicks are answered by the node handlers
        during the poll."""
        if not isinstance(item, Key) or item.alt or item.meta:
            return
        name = item.name
        if item.ctrl:
            if name == "c":
                self.exit = True
            return
        if name == "q":
            self.exit = True
        elif name == "escape":
            self.back()
        elif name in ("j", "down"):
            self.move(1)
        elif name in ("k", "up"):
            self.move(-1)
        elif (name == "g" and not item.shift) or name == "home":
            self.move_to(0)
        elif name == "g" or name == "end":
            self.move_to(-1)
        elif name in ("enter", "o"):
            self.act(self.open_post)
        elif name == "c":
            self.act(self.copy_post)
        elif name == "s":
            self.act(self.share_post)
        elif name == "t":
            self.step_kind(-1 if item.shift else 1)
        elif name == "x":
            self.toggle_failed()
        elif name == "0":
            self.clear()
        elif name in "123456789" and len(name) == 1:
            index = int(name) - 1
            if index < len(self.agents):
                self.toggle(self.agents[index])
        elif name == "?":
            self.toggle_keys()

    def render(self) -> None:
        assert self.surface
        # Filter in the query, so a selected agent's older posts still show.
        narrow = {"agents": self.selected, "kind": self.kind, "failed": self.failed_only}
        posts = self.store.list(**narrow)
        agents = self.store.agents()
        counts = (self.store.count(**narrow), self.store.count())
        self._follow(posts)
        midnight = dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
        today = self.store.since(int(midnight))
        live = time.monotonic() < self.live_until
        page = views.feed_view(
            posts, agents, self.selected, self.handlers, live=live, counts=counts, today=today,
            numbers=labels.numbers_for(posts, self.store), kind=self.kind, failed_only=self.failed_only,
            cursor=self.cursor, keys=self.keys,
        )  # fmt: skip
        if self.toast:
            text, sub = self.toast
            page["layer"] = ui.col(ui.toast(text, sub=sub, ttl=TOAST, key="toast"), key="toasts")
        self.surface.render(**page)
        self.posts = posts
        self.agents = agents + sorted(agent for agent in self.selected if agent not in agents)
        if self._reveal is not None:
            self.surface.reveal(*self._reveal)
            self._reveal = None
        if tuple(agents) != self._styled:
            self._styled = tuple(agents)  # a new agent gets its color rule, without a restart
            self.surface.stylesheet("feed-block", design.stylesheet(agents))

    def _follow(self, posts: list[Post]) -> None:
        """Keeps the cursor on a post that is still shown: its own, else the one now in its
        place (a session summary replaced, a filter changed), else none."""
        if self.cursor is None or any(post.id == self.cursor for post in posts):
            return
        index = next((i for i, post in enumerate(self.posts) if post.id == self.cursor), 0)
        survivors = [post for post in self.posts[index:] if any(p.id == post.id for p in posts)]
        self.cursor = (
            survivors[0].id if survivors else (posts[min(index, len(posts) - 1)].id if posts else None)
        )

    # -- the keyboard

    def move(self, step: int) -> None:
        """Moves the cursor a post down (1) or up (-1); the first move lands on the top post."""
        if not self.posts:
            return
        ids = [post.id for post in self.posts]
        if self.cursor not in ids:
            self.move_to(0)
            return
        self.move_to(max(0, min(len(ids) - 1, ids.index(self.cursor) + step)))

    def move_to(self, index: int) -> None:
        """Puts the cursor on the post at `index` (-1 is the last shown) and scrolls to it."""
        if not self.posts:
            return
        index = index % len(self.posts)
        self.cursor = self.posts[index].id
        # At the top, the strip comes into view with the post.
        self._reveal = (
            ("main.feed.strip", "start") if index == 0 else (f"main.feed.p{self.cursor}", "nearest")
        )
        self.dirty = True

    def act(self, action) -> None:
        """Runs a post action on the cursor's post; without a cursor, says how to get one."""
        post = next((post for post in self.posts if post.id == self.cursor), None)
        if post is None:
            self._say("no post selected", "j or k picks one")
            return
        action(post)

    def back(self) -> None:
        """Esc: closes the legend, else clears the filters, else drops the cursor, else leaves."""
        if self.keys:
            self.toggle_keys()
        elif self.filtering:
            self.clear()
        elif self.cursor is not None:
            self.cursor = None
            self.dirty = True
        else:
            self.exit = True

    @property
    def filtering(self) -> bool:
        return bool(self.selected or self.kind or self.failed_only)

    def _changed(self) -> None:
        """A filter or the legend changed: both show in the strip, so the strip comes
        into view (a moved cursor may have scrolled it away)."""
        self._reveal = ("main.feed.strip", "start")
        self.dirty = True

    # -- filters

    def toggle(self, agent: str) -> None:
        if agent in self.selected:
            self.selected.discard(agent)
        else:
            self.selected.add(agent)
        self._changed()

    def toggle_kind(self, kind: str) -> None:
        """Shows only this kind, or every kind again when it is the one shown."""
        self.kind = None if self.kind == kind else kind
        self._changed()

    def step_kind(self, step: int) -> None:
        """`t`: the next kind the feed has (`T` the previous), then every kind again."""
        have = set(self.store.kinds())
        cycle = [None, *(kind for kind in KIND_ORDER if kind in have)]
        at = cycle.index(self.kind) if self.kind in cycle else 0
        self.kind = cycle[(at + step) % len(cycle)]
        self._changed()

    def toggle_failed(self) -> None:
        self.failed_only = not self.failed_only
        self._changed()

    def clear(self) -> None:
        """Every filter off: all agents, every kind, failed or not."""
        self.selected.clear()
        self.kind = None
        self.failed_only = False
        self._changed()

    def toggle_keys(self) -> None:
        self.keys = not self.keys
        self._changed()

    # -- actions

    def _say(self, text: str, sub: str = "") -> None:
        self.toast = (text, sub)
        self.toast_until = time.monotonic() + TOAST
        self.dirty = True

    def open_post(self, post: Post) -> None:
        if not post.link:
            self._say("nothing to open", "this post has no link")
        elif not openable(post.link):
            self._say("not a web link", post.link)
        elif open_url(post.link):
            self._say("opened", post.link)
        else:
            self._say("could not open a browser", post.link)

    def copy_post(self, post: Post) -> None:
        text = views.summary(post)
        if clipboard(text):
            self._say("copied to the clipboard", text.splitlines()[0])
        else:
            self._say("no clipboard tool", "install pbcopy, wl-copy or xclip")

    def share_post(self, post: Post) -> None:
        url = views.intent_url(views.summary(post))
        if open_url(url):
            self._say("share opened on x.com", "post it from there")
        else:
            self._say("could not open a browser", url)
