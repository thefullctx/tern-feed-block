"""The timeline as native nodes: the rail, day dividers, the filter strip.

X's structure in Tern's console idiom: `@handle ······ 2m` over the title, a
muted one-line body, and the PR or commit in a raised well with a hairline
edge. The rail is a hairline in the gutter with one glyph per post, tinted by
the agent's color. Actions show on hover or focus only.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from tern_sdk import ui

from . import colors, labels
from .reltime import day_key, day_label, relative
from .store import Post

# A number to show in the well, from a PR/issue link.
NUMBER = re.compile(r"(?:#|/pull/|/issues/)(\d+)")
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


def _leader(key: str):
    """The dotted run between the handle and the time."""
    return ui.html.div(role="leader", class_="fb-leader", key=key)


def post_node(
    post: Post, handlers: Handlers, *, now: float, newest: bool, handle: str | None = None
) -> ui.Node:
    """One post on the rail: the gutter glyph, then the head line, title, body, well, actions."""
    classes = [colors.agent_class(post.agent)]
    if post.failed:
        classes.append("fb-failed")
    if newest:
        classes.append("fb-new")
    head = ui.row(
        ui.html.span(
            handle or f"@{post.agent}", class_=colors.agent_class(post.agent), role="handle", key="h"
        ),
        _leader("l"),
        ui.text(relative(post.created_at, now), role="time", key="t"),
        role="head",
        key="head",
    )
    children = [head, ui.text(post.title, role="title", key="ti")]
    if post.body:
        children.append(ui.text(post.body.splitlines()[0], role="body", key="b"))
    well = []
    if post.repo:
        well.append(ui.text(post.repo, role="well-repo", key="r"))
    if post.branch:
        well.append(ui.text(post.branch, role="well-branch", key="br"))
    tag = number(post.link)
    if tag:
        well.append(ui.text(tag, role="well-number", key="n"))
    if post.diffstat:
        well.append(ui.text(f"+{post.additions or 0}", role="diff-add", key="a"))
        well.append(ui.text(f"−{post.deletions or 0}", role="diff-del", key="d"))
    if post.link:
        well.append(ui.text(post.link, role="well-link", key="k"))
    if well:
        children.append(ui.row(*well, role="well", key="w"))
    actions = []
    if post.link:
        actions.append(
            ui.text(
                "open",
                role="action",
                key="open",
                title="open the link",
                on_click=lambda _, p=post: handlers.open(p),
            )
        )
    actions.append(
        ui.text(
            "copy",
            role="action",
            key="copy",
            title="copy the post",
            on_click=lambda _, p=post: handlers.copy(p),
        )
    )
    actions.append(
        ui.text(
            "share",
            role="action",
            key="share",
            title="share on X",
            on_click=lambda _, p=post: handlers.share(p),
        )
    )
    children.append(ui.row(*actions, role="actions", key="ac"))
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
    agents: list[str], selected: set[str], shown: int, total: int, live: bool, handlers: Handlers
) -> ui.Node:
    """The top strip: the live dot, the wordmark, and agent handles as filters."""
    chips = [
        ui.html.span(
            f"@{agent}",
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
        ui.text(f"{shown}/{total}" if total else "", role="count", key="count"),
        role="strip",
        key="strip",
    )


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
) -> dict:
    """The pane: the strip over the timeline, filtered by the selected agents.

    `counts` is (matching, total) across the whole store; without it the strip
    counts the posts it was given.
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
            body.extend(
                post_node(
                    post, handlers, now=now, newest=post.id == newest, handle=labels.handle(post, numbers)
                )
                for post in day
            )
    return {
        # The region root's own props are not drawn, so the scoped column is its child.
        "main": ui.col(
            ui.col(
                strip(agents, selected, matching, total, live, handlers),
                *body,
                role="feed",
                key="feed",
            ),
            key="root",
        )
    }
