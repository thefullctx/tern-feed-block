"""The Tern surface: the feed as a live pane, without a restart.

A store watcher (a query for the newest id, four times a second) is the whole
live story: writers are other processes, so polling the local file is both
simpler and safer than any push mechanism. Input never blocks on it.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import webbrowser

from tern_sdk import Key, ui

from . import design, views
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
        self.handlers = Handlers(
            open=self.open_post, copy=self.copy_post, share=self.share_post, toggle=self.toggle
        )
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
        """Keys leave; clicks are answered by the node handlers during the poll."""
        if isinstance(item, Key) and (item.name in ("q", "escape") or (item.ctrl and item.name == "c")):
            self.exit = True

    def render(self) -> None:
        assert self.surface
        posts = self.store.list()
        live = time.monotonic() < self.live_until
        page = views.feed_view(posts, None, self.selected, self.handlers, live=live)
        if self.toast:
            text, sub = self.toast
            page["layer"] = ui.col(ui.toast(text, sub=sub, ttl=TOAST, key="toast"), key="toasts")
        self.surface.render(**page)
        agents = tuple(views.agents_of(posts))
        if agents != self._styled:
            self._styled = agents  # a new agent gets its color rule, without a restart
            self.surface.stylesheet("feed-block", design.stylesheet(list(agents)))

    # -- actions

    def toggle(self, agent: str) -> None:
        if agent in self.selected:
            self.selected.discard(agent)
        else:
            self.selected.add(agent)
        self.dirty = True

    def _say(self, text: str, sub: str = "") -> None:
        self.toast = (text, sub)
        self.toast_until = time.monotonic() + TOAST
        self.dirty = True

    def open_post(self, post: Post) -> None:
        if post.link and open_url(post.link):
            self._say("opened", post.link)
        else:
            self._say("nothing to open", "this post has no link")

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
