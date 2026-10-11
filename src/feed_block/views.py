"""The timeline as native nodes: avatars, day dividers, the filter strip.

A social feed in Tern's console idiom, one post per line pair: an avatar
(Tern's builtin mark, else the agent's tinted initials) with its kind badge
beside a head line (name `@handle` · 2m · kind, the actions pushed right, on
hover or focus only), the title, a muted body and the card (repo · branch,
the short reference, the diff bar), sized to its content. A hairline closes
every post but one: a session posting again right below itself continues the
thread instead, under the same avatar column, until its last post.
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


def diff_bar(adds: int | None, dels: int | None) -> tuple[int, int]:
    """A ten-cell bar as (green cells, red cells) for `+adds −dels`.

    Green takes `round(10 * adds / (adds + dels))`, clamped so either non-zero
    side keeps at least one cell; a one-sided diff takes all ten. Nothing when
    there is no diff at all.
    """
    adds, dels = adds or 0, dels or 0
    if not adds and not dels:
        return (0, 0)
    if not dels:
        return (10, 0)
    if not adds:
        return (0, 10)
    green = max(1, min(9, round(10 * adds / (adds + dels))))
    return (green, 10 - green)


# One block per diff-bar cell; both runs use the same, so it reads as a bar.
DIFF_BLOCK = "■"


def _card(post: Post) -> ui.Node | None:
    """The work card: repo · branch with the short reference at the right, then the diff bar."""
    top: list[ui.Node] = []
    if post.repo:
        top.append(ui.text(post.repo, role="card-repo", key="r"))
    if post.branch:
        top.append(ui.text(post.branch, role="card-branch", key="br"))
    ref = reference(post.link)
    if ref:
        top.append(ui.text(ref, role="card-ref", key="n"))
    if top:
        # The reference reads to the right, away from the work.
        rows = [ui.row(*top, role="card-top", key="top")]
    else:
        rows = []
    green, red = diff_bar(post.additions, post.deletions) if post.diffstat else (0, 0)
    if green or red:
        rows.append(
            ui.row(
                ui.text(DIFF_BLOCK * green, role="diff-bar-add", key="ga"),
                ui.text(DIFF_BLOCK * red, role="diff-bar-del", key="gr"),
                ui.text(f"+{post.additions or 0}", role="diff-add", key="a"),
                ui.text(f"−{post.deletions or 0}", role="diff-del", key="d"),
                role="card-diff",
                key="df",
            )
        )
    return ui.col(*rows, role="card", key="w") if rows else None


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
    joined: bool = False,
) -> ui.Node:
    """One post: an avatar with its kind badge, beside the head line, title, body and card.

    `continued` is the same session posting again right below: it keeps the
    empty avatar column but no avatar, so its text lines up under the post
    above. `joined` is the mirror image — the post below is continued — and
    drops the hairline under this post, so the thread reads as one unit.
    """
    classes = [colors.agent_class(post.agent)]
    if post.failed:
        classes.append("fb-failed")
    if newest:
        classes.append("fb-new")
    if continued:
        classes.append("fb-cont")
    if joined:
        classes.append("fb-joined")
    head = []
    if not continued:
        head.append(ui.html.span(colors.display_name(post.agent), role="name", key="n"))
        head.append(ui.html.span(handle or f"@{post.agent}", role="handle", key="h"))
    head.append(ui.text(relative(post.created_at, now), role="time", key="t"))
    head.append(ui.text(post.kind, role="kind", key="k"))
    actions = []
    if post.link:
        actions.append(_action("↗ open", "open the link", lambda p=post: handlers.open(p), "open"))
    actions.append(_action("⧉ copy", "copy the post", lambda p=post: handlers.copy(p), "copy"))
    actions.append(_action("⤴ share", "share on X", lambda p=post: handlers.share(p), "share"))
    head.append(ui.row(*actions, role="actions", key="ac"))
    children = [ui.row(*head, role="head", key="head"), ui.text(post.title, role="title", key="ti")]
    # The body: one muted line, between the title and the card.
    if post.body:
        children.append(ui.text(post.body.splitlines()[0], role="body", key="b"))
    # The card: the work at the right height, or nothing when there is none.
    card = _card(post)
    if card:
        children.append(card)
    gutter: list[ui.Node] = []
    if not continued:
        # The avatar: Tern's builtin mark when it carries one, else the agent's
        # initials in its own hue; the kind's badge rides the lower right edge.
        name = colors.display_name(post.agent)
        icon = colors.mark(post.agent)
        face: list[ui.Node] = (
            [ui.text(colors.initials(post.agent), role="letter", key="l")]
            if icon is None
            else [ui.icon(icon, role="mark", key="m")]
        )
        gutter.append(
            ui.html.div(
                *face,
                ui.html.span(colors.glyph(post.kind, post.failed), role="badge", key="bg"),
                role="avatar",
                key="av",
                title=name,
                aria=name,
            )
        )
    return ui.html.div(
        # The avatar column: the same width on every post, empty on continuations.
        ui.html.div(*gutter, role="gutter", key="gu"),
        ui.html.div(*children, role="content", key="c"),
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
    """The agents in the feed, most recent first, for the filter strip."""
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
            for position, post in enumerate(day):
                below = day[position + 1] if position + 1 < len(day) else None
                # A session posting again right below itself reads as one thread:
                # no handle and no avatar on it, and no hairline under its last run.
                continued = bool(
                    post.session and above and (above.agent, above.session) == (post.agent, post.session)
                )
                body.append(
                    post_node(
                        post,
                        handlers,
                        now=now,
                        newest=post.id == newest,
                        handle=labels.handle(post, numbers),
                        continued=continued,
                        joined=bool(
                            post.session
                            and below
                            and (post.agent, post.session) == (below.agent, below.session)
                        ),
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
