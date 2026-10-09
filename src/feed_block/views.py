"""The timeline as native nodes: the rail, day dividers, the filter strip.

X's structure in Tern's console idiom, three lines a post: `@handle · 2m · kind`
(actions at the right, on hover or focus only), the title, then a muted body
beside a raised well (repo · branch · #42 · +adds −dels). The rail is a
hairline in the gutter with one glyph per post, tinted by the agent's color;
a session posting again right below itself drops its handle, as a thread.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, urlsplit

from tern_sdk import ui

from . import colors, labels
from .reltime import day_key, day_label, relative
from .store import Post

# A number to show in the well, from a PR/issue link.
NUMBER = re.compile(r"(?:#|/pull/|/issues/|/merge_requests/)(\d+)")
# A commit page on GitHub, GitLab or Bitbucket.
COMMIT = re.compile(r"/commits?/([0-9a-f]{7,40})\b")
EMPTY = "the feed is empty · post with `feed post --agent you --kind note --title …` or the MCP `post` tool"


@dataclass
class Handlers:
    """What the pane's clicks do; the surface owns the side effects."""

    open: Callable[[Post], object]
    copy: Callable[[Post], object]
    share: Callable[[Post], object]
    toggle: Callable[[str], object]


def number(link: str | None) -> str | None:
    """`#42` from a pull or issue link, when the link has one."""
    found = NUMBER.search(link or "")
    return f"#{found.group(1)}" if found else None


def summary(post: Post) -> str:
    """The post as one shareable line: what the agent did, and the link."""
    text = post.title
    if post.repo:
        text += f" · {post.repo}"
    if post.diffstat:
        text += f" · {post.diffstat}"
    return f"{text}\n{post.link}" if post.link else text


def intent_url(text: str) -> str:
    """X's share intent, prefilled; no API and no keys."""
    return f"https://x.com/intent/tweet?text={quote(text, safe='')}"


def reference(link: str | None) -> str | None:
    """A link, short: `#44` for a PR or issue, `d1eeb2a` for a commit, else its host."""
    if not link:
        return None
    tag = number(link)
    if tag:
        return tag
    found = COMMIT.search(link)
    if found:
        return found.group(1)[:7]
    return urlsplit(link).netloc or None


def _action(label: str, title: str, run: Callable[[], object], key: str) -> ui.Node:
    return ui.text(label, role="action", key=key, title=title, on_click=lambda _: run())


def post_node(
    post: Post,
    handlers: Handlers,
    *,
    now: float,
    newest: bool,
    handle: str | None = None,
    continued: bool = False,
) -> ui.Node:
    """One post on the rail: the glyph, a head line, the title, then the details in one line.

    `continued` is the same session posting again right below: the head drops the
    handle, so a run of one session's posts reads as one thread.
    """
    classes = [colors.agent_class(post.agent)]
    if post.failed:
        classes.append("fb-failed")
    if newest:
        classes.append("fb-new")
    if continued:
        classes.append("fb-cont")
    head = []
    if not continued:
        head.append(
            ui.html.span(
                handle or f"@{post.agent}", class_=colors.agent_class(post.agent), role="handle", key="h"
            )
        )
    head.append(ui.text(relative(post.created_at, now), role="time", key="t"))
    head.append(ui.text(post.kind, role="kind", key="k"))
    actions = []
    if post.link:
        actions.append(_action("open", "open the link", lambda p=post: handlers.open(p), "open"))
    actions.append(_action("copy", "copy the post", lambda p=post: handlers.copy(p), "copy"))
    actions.append(_action("share", "share on X", lambda p=post: handlers.share(p), "share"))
    head.append(ui.row(*actions, role="actions", key="ac"))
    children = [ui.row(*head, role="head", key="head"), ui.text(post.title, role="title", key="ti")]

    # The details: the body (muted), then the well: repo · branch · ref · +adds −dels.
    details = []
    if post.body:
        details.append(ui.text(post.body.splitlines()[0], role="body", key="b"))
    well = []
    if post.repo:
        well.append(ui.text(post.repo, role="well-repo", key="r"))
    if post.branch:
        well.append(ui.text(post.branch, role="well-branch", key="br"))
    ref = reference(post.link)
    if ref:
        well.append(ui.text(ref, role="well-ref", key="n"))
    if post.diffstat:
        well.append(
            ui.row(
                ui.text(f"+{post.additions or 0}", role="diff-add", key="a"),
                ui.text(f"−{post.deletions or 0}", role="diff-del", key="d"),
                role="well-diff",
                key="df",
            )
        )
    if well:
        details.append(ui.row(*well, role="well", key="w"))
    if details:
        children.append(ui.row(*details, role="details", key="de"))
    return ui.html.div(
        # The rail: the kind's glyph, on a hairline that runs the post's full height.
        ui.html.div(
            ui.text(colors.glyph(post.kind, post.failed), role="glyph", key="g"),
            class_="fb-rail",
            role="rail",
            key="rail",
        ),
        ui.html.div(*children, class_="fb-content", role="content", key="c"),
        class_=" ".join(classes),
        role=f"post-{post.kind}",
        key=f"p{post.id}",
    )


def strip(
    agents: list[str],
    selected: set[str],
    shown: int,
    total: int,
    live: bool,
    handlers: Handlers,
    today: tuple[dict[str, int], int] = ({}, 0),
) -> ui.Node:
    """The top strip: the live dot, the wordmark, agent handles as filters, today's counts.

    `today` is (posts per agent today, failed posts today).
    """
    per_agent, failed = today
    chips = [
        ui.html.span(
            f"@{agent}" + (f" {per_agent[agent]}" if per_agent.get(agent) else ""),
            class_=colors.agent_class(agent),
            role="chip-on" if agent in selected else "chip",
            key=f"chip-{colors.slug(agent)}",
            title="filter the feed" if agent not in selected else "remove this filter",
            on_click=lambda _, a=agent: handlers.toggle(a),
        )
        for agent in agents
    ]
    return ui.row(
        ui.text("●", role="live-fresh" if live else "live", key="live"),
        ui.text("feed-block", role="wordmark", key="wordmark"),
        *chips,
        ui.text(f"✗ {failed} failed today" if failed else "", role="failed-today", key="failed"),
        ui.text(f"{shown}/{total}" if total else "", role="count", key="count"),
        role="strip",
        key="strip",
    )


def today_counts(posts: list[Post], now: float) -> tuple[dict[str, int], int]:
    """Posts per agent and failed posts, among the given posts from today."""
    today = day_key(now)
    per_agent: dict[str, int] = {}
    failed = 0
    for post in posts:
        if day_key(post.created_at) == today:
            per_agent[post.agent] = per_agent.get(post.agent, 0) + 1
            failed += post.failed
    return per_agent, failed


def agents_of(posts: list[Post]) -> list[str]:
    """The agents on the rail, most recent first, for the filter strip."""
    seen: dict[str, int] = {}
    for post in posts:
        seen.setdefault(post.agent, post.id)
    return sorted(seen, key=lambda agent: -seen[agent])


def feed_view(
    posts: list[Post],
    agents: list[str] | None,
    selected: set[str],
    handlers: Handlers,
    *,
    live: bool = False,
    now: float | None = None,
    counts: tuple[int, int] | None = None,
    today: tuple[dict[str, int], int] | None = None,
) -> dict:
    """The pane: the strip over the timeline, filtered by the selected agents.

    `counts` is (matching, total) and `today` is (posts per agent, failed posts)
    across the whole store; without them the strip counts the posts it was given.
    """
    now = time.time() if now is None else now
    agents = agents_of(posts) if agents is None else list(agents)
    # A selected agent keeps its chip, so a filter can always be cleared.
    agents += sorted(agent for agent in selected if agent not in agents)
    shown = [post for post in posts if not selected or post.agent in selected]
    matching, total = counts if counts is not None else (len(shown), len(posts))
    newest = shown[0].id if shown else 0
    body: list[ui.Node] = []
    if not total:
        body.append(ui.text(EMPTY, role="empty", key="empty"))
    elif not shown:
        body.append(
            ui.text(
                "no posts from the selected agents · tap a handle to clear it", role="empty", key="filtered"
            )
        )
    else:
        numbers = labels.session_numbers(shown)
        groups: dict[str, tuple[str, list[Post]]] = {}
        for post in shown:
            key = day_key(post.created_at).isoformat()
            groups.setdefault(key, (day_label(post.created_at, now), []))[1].append(post)
        for index, (key, (label, day)) in enumerate(groups.items()):
            body.append(ui.row(ui.text(label, role="day-label", key=f"dl{index}"), role="day", key=f"d{key}"))
            above: Post | None = None
            for post in day:
                # A session posting again right below itself reads as one thread.
                continued = bool(
                    above and post.session and (above.agent, above.session) == (post.agent, post.session)
                )
                body.append(
                    post_node(
                        post,
                        handlers,
                        now=now,
                        newest=post.id == newest,
                        handle=labels.handle(post, numbers),
                        continued=continued,
                    )
                )
                above = post
    return {
        # The region root's own props are not drawn, so the scoped column is its child.
        "main": ui.col(
            ui.col(
                strip(
                    agents,
                    selected,
                    matching,
                    total,
                    live,
                    handlers,
                    today if today is not None else today_counts(posts, now),
                ),
                *body,
                role="feed",
                key="feed",
            ),
            key="root",
        )
    }
