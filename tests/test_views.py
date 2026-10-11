"""The timeline's nodes: the avatar, the card, the strip, and the share summary."""

from __future__ import annotations

import time

from tern_sdk.reconcile import View

from feed_block import views
from feed_block.reltime import day_label
from feed_block.store import Post
from feed_block.views import Handlers


def noop(*args):
    pass


HANDLERS = Handlers(open=noop, copy=noop, share=noop, toggle=noop)


def post(**kw) -> Post:
    base = dict(
        id=7,
        agent="hermes",
        kind="pr",
        title="pushed PR #42 to handover",
        body="the body line",
        repo="handover",
        link="https://github.com/x/handover/pull/42",
        additions=120,
        deletions=34,
        status="ok",
        created_at=int(time.time()) - 120,
    )
    base.update(kw)
    return Post(**base)  # type: ignore[arg-type]


def built(page) -> View:
    return View.build(page)


def nodes(page) -> dict:
    return built(page).nodes()


def gutter(node):
    return [c for c in node.children if c.props.get("role") == "gutter"][0]


def content(node):
    return [c for c in node.children if c.props.get("role") == "content"][0]


def find(node, role):
    """The first node under `node` (itself included) with this role."""
    if node.props.get("role") == role:
        return node
    for child in node.children:
        found = find(child, role)
        if found is not None:
            return found
    return None


def chip_texts(tree) -> list[str]:
    return [n.props["text"] for n in tree.values() if n.props.get("role", "").startswith("chip")]


def test_a_post_is_a_frameless_el():
    node = views.post_node(post(), HANDLERS, now=time.time(), newest=True)
    wire = node.to_json()
    assert wire["k"] == "el"
    assert wire["p"]["tag"] == "div"
    assert wire["p"]["role"] == "post-pr"
    classes = wire["p"]["class"].split()
    assert "fb-agent-hermes" in classes and "fb-new" in classes and "fb-failed" not in classes
    assert wire["p"]["key"] == "p7"


def test_failed_post_is_marked_and_keeps_its_kind():
    node = views.post_node(post(status="failed", kind="test"), HANDLERS, now=time.time(), newest=False)
    wire = node.to_json()
    assert wire["p"]["role"] == "post-test"
    assert "fb-failed" in wire["p"]["class"].split()
    assert "fb-new" not in wire["p"]["class"].split()


def test_head_line_is_name_handle_time_kind_then_actions():
    node = views.post_node(post(), HANDLERS, now=time.time(), newest=False)
    head = find(node, "head")
    assert [c.props.get("role") for c in head.children] == ["name", "handle", "time", "kind", "actions"]
    assert [c.props.get("text") for c in head.children[:4]] == ["Hermes", "@hermes", "2m", "pr"]


def test_a_continued_post_drops_its_handle_and_avatar():
    node = views.post_node(post(), HANDLERS, now=time.time(), newest=False, continued=True)
    assert [c.props.get("role") for c in find(node, "head").children] == ["time", "kind", "actions"]
    assert "fb-cont" in node.props["class"].split()
    # The avatar column stays, empty, so the text lines up under the post above.
    assert gutter(node).children == []


def test_a_post_joined_above_keeps_a_divider_only_when_its_thread_ends():
    node = views.post_node(post(), HANDLERS, now=time.time(), newest=False, joined=True)
    assert "fb-joined" in node.props["class"].split()
    plain = views.post_node(post(), HANDLERS, now=time.time(), newest=False, joined=False)
    assert "fb-joined" not in plain.props["class"].split()


def test_display_names_are_the_known_ones_or_the_name_title_cased():
    assert [views.colors.display_name(a) for a in ("claude-code", "codex", "omp", "pi", "hermes")] == [
        "Claude Code",
        "Codex",
        "OMP",
        "pi",
        "Hermes",
    ]
    assert views.colors.display_name("my_agent-x") == "My Agent X"


def test_tern_marks_cover_the_known_agents_and_nobody_else():
    assert [views.colors.mark(a) for a in ("claude-code", "codex", "omp", "pi")] == [
        "app-claude",
        "app-codex",
        "app-omp",
        "pi-mark",
    ]
    assert views.colors.mark("hermes") is None
    assert views.colors.mark("my-agent") is None


def test_fallback_initials_are_up_to_two_letters():
    assert views.colors.initials("claude-code") == "CC"
    assert views.colors.initials("hermes") == "H"
    assert views.colors.initials("pi") == "P"
    assert views.colors.initials("my_agent-x") == "MA"


def test_the_avatar_is_a_tern_mark_or_initials_with_the_badge():
    # hermes has no builtin mark: the initials avatar
    node = views.post_node(post(kind="merge"), HANDLERS, now=time.time(), newest=False)
    avatar = gutter(node).children[0]
    assert gutter(node).props["role"] == "gutter" and avatar.props["role"] == "avatar"
    assert avatar.props["title"] == "Hermes" and avatar.props["aria"] == "Hermes"
    letter, badge = avatar.children
    assert (letter.props["role"], letter.props["text"]) == ("letter", "H")
    assert (badge.props["role"], badge.props["text"]) == ("badge", "⋈")  # a join, not a check mark
    assert content(node) is node.children[1]

    # claude-code carries tern's mark; the badge rides beside it either way
    marked = views.post_node(post(agent="claude-code"), HANDLERS, now=time.time(), newest=False)
    face, badge = gutter(marked).children[0].children
    assert face.props["role"] == "mark" and face.props.get("name") == "app-claude"
    assert face.children == [] and badge.props["text"] == "⇡"

    # a multi-word unknown agent gets both initials
    double = views.post_node(post(agent="unknown-bird"), HANDLERS, now=time.time(), newest=False)
    face = gutter(double).children[0].children[0]
    assert (face.props["role"], face.props["text"]) == ("letter", "UB")


def test_the_failed_badge_chip_wears_the_failed_glyph():
    node = views.post_node(post(kind="commit", status="failed"), HANDLERS, now=time.time(), newest=False)
    badge = find(node, "avatar").children[-1]
    assert badge.props["role"] == "badge" and badge.props["text"] == "✗"
    assert "fb-failed" in node.props["class"].split()  # the ring and title take the failure's hue in css
    healthy = views.post_node(post(kind="commit", status="ok"), HANDLERS, now=time.time(), newest=False)
    chip = find(healthy, "badge")
    assert chip.props["text"] == "●" and "fb-failed" not in healthy.props["class"].split()


def test_the_card_shows_repo_branch_ref_and_diff_without_the_url():
    node = views.post_node(post(branch="main"), HANDLERS, now=time.time(), newest=False)
    card = find(node, "card")
    assert [c.props.get("role") for c in card.children] == ["card-top", "card-diff"]
    assert card.kind == "col"
    top = find(card, "card-top")
    assert [c.props.get("role") for c in top.children] == ["card-repo", "card-branch", "card-ref"]
    assert find(card, "card-ref").props["text"] == "#42"
    diff = find(card, "card-diff")
    assert [c.props.get("text") for c in diff.children] == [
        views.DIFF_BLOCK * 8,
        views.DIFF_BLOCK * 2,
        "+120",
        "−34",
    ]
    assert "github.com" not in str(card.to_json())


def test_the_card_body_is_head_title_body_card_flow():
    node = views.post_node(post(branch="main"), HANDLERS, now=time.time(), newest=False)
    content = find(node, "content")
    assert [c.props.get("role") for c in content.children] == ["head", "title", "body", "card"]
    # Nothing wraps the card any more: it hangs off the content flow itself,
    # which is what lets the stylesheet hug it to its content.
    assert [c.kind for c in content.children] == ["row", "text", "text", "col"]


def test_diff_bar_splits_ten_cells_between_the_sides():
    assert views.diff_bar(486, 139) == (8, 2)
    assert views.diff_bar(120, 34) == (8, 2)
    assert views.diff_bar(40, 88) == (3, 7)
    # A one-sided diff takes all ten; either non-zero side keeps one.
    assert views.diff_bar(5, 0) == (10, 0)
    assert views.diff_bar(0, 7) == (0, 10)
    assert views.diff_bar(99, 1) == (9, 1)
    assert views.diff_bar(1, 99) == (1, 9)
    assert views.diff_bar(0, 0) == (0, 0)


def test_reference_is_short():
    assert views.reference("https://github.com/x/y/pull/42") == "#42"
    assert views.reference("https://gitlab.com/g/p/-/merge_requests/9") == "#9"
    assert views.reference("https://github.com/x/y/commit/d1eeb2ad9b59e6fcc") == "d1eeb2a"
    assert views.reference("https://bitbucket.org/x/y/commits/abcdef1234") == "abcdef1"
    assert views.reference("https://ci.example.com/run/5") == "ci.example.com"
    assert views.reference(None) is None


def test_card_is_skipped_without_anything_to_show():
    node = views.post_node(
        post(repo=None, link=None, additions=None, deletions=None), HANDLERS, now=time.time(), newest=False
    )
    assert not any(c.props.get("role") == "card" for c in content(node).children)
    # A branch alone still makes a card, without a reference.
    bare = views.post_node(
        post(branch="main", repo=None, link=None, additions=None, deletions=None),
        HANDLERS,
        now=time.time(),
        newest=False,
    )
    top = find(bare, "card-top")
    assert top.children[0].props["role"] == "card-branch"
    assert find(bare, "card-diff") is None


def test_the_failed_card_still_carries_its_work():
    node = views.post_node(
        post(status="failed", branch="main", link=None, additions=None, deletions=None),
        HANDLERS,
        now=time.time(),
        newest=False,
    )
    assert "fb-failed" in node.props["class"].split()
    assert find(node, "card") is not None


def test_body_is_one_muted_line():
    node = views.post_node(post(body="first\nsecond"), HANDLERS, now=time.time(), newest=False)
    assert find(node, "body").props["text"] == "first"


def test_actions_are_open_copy_share_and_open_needs_a_link():
    node = views.post_node(post(), HANDLERS, now=time.time(), newest=False)
    actions = find(node, "actions")
    assert [c.props["text"] for c in actions.children] == ["↗ open", "⧉ copy", "⤴ share"]

    bare = views.post_node(post(link=None), HANDLERS, now=time.time(), newest=False)
    actions = find(bare, "actions")
    assert [c.props["text"] for c in actions.children] == ["⧉ copy", "⤴ share"]


def test_every_post_carries_its_kind_on_its_root():
    for kind in ("commit", "pr", "merge", "test", "deploy", "note", "session"):
        node = views.post_node(post(kind=kind), HANDLERS, now=time.time(), newest=False)
        assert node.props["role"] == f"post-{kind}"


def test_number_finds_pulls_and_issues_only():
    assert views.number("https://github.com/x/y/pull/42") == "#42"
    assert views.number("https://github.com/x/y/issues/7") == "#7"
    assert views.number("https://github.com/x/y/commit/abcdef") is None
    assert views.number(None) is None


def test_summary_is_one_shareable_line():
    assert (
        views.summary(post())
        == "pushed PR #42 to handover · handover · +120 −34\nhttps://github.com/x/handover/pull/42"
    )
    assert (
        views.summary(post(repo=None, link=None, additions=None, deletions=None))
        == "pushed PR #42 to handover"
    )
    assert (
        views.summary(post(title="fixed", link="https://x.test", repo=None, additions=None, deletions=None))
        == "fixed\nhttps://x.test"
    )


def test_intent_url_prefills_x_without_keys():
    url = views.intent_url("a line\nhttps://x.test")
    assert url.startswith("https://x.com/intent/tweet?text=")
    assert "%0A" in url and "%20" in url and "&" not in url.split("text=")[1]


def test_strip_lists_agents_as_filters_with_the_live_dot():
    page = views.feed_view(
        [post(), post(id=8, agent="codex", kind="test", title="green")],
        ["hermes", "codex"],
        set(),
        HANDLERS,
        live=True,
    )
    tree = nodes(page)
    strip = tree["main.feed.strip"]
    assert strip.props["role"] == "strip"
    assert [text.split()[0] for text in chip_texts(tree)] == ["@hermes", "@codex"]
    assert any(n.props.get("role") == "live-fresh" for n in tree.values())
    assert tree["main.feed.strip.count"].props["text"] == "2/2"


def test_selected_agent_filters_the_timeline():
    posts = [post(), post(id=8, agent="codex", kind="test", title="green build")]
    page = views.feed_view(posts, ["hermes", "codex"], {"codex"}, HANDLERS)
    tree = nodes(page)
    assert "main.feed.p8" in tree and "main.feed.p7" not in tree
    assert tree["main.feed.strip.count"].props["text"] == "1/2"


def test_filtering_everything_out_says_so():
    page = views.feed_view([post()], ["hermes"], {"codex"}, HANDLERS)
    tree = nodes(page)
    assert tree["main.feed.filtered"].props["role"] == "empty"


def test_day_dividers_break_up_the_feed():
    today = int(time.time())
    older = today - 3 * 86400
    posts = [post(id=9, created_at=today), post(id=8, created_at=older, title="older work")]
    page = views.feed_view(posts, ["hermes"], set(), HANDLERS, now=float(today))
    tree = nodes(page)
    labels = sorted(n.props.get("text", "") for n in tree.values() if n.props.get("role") == "day-label")
    assert labels == sorted(["today", day_label(older, today)])
    assert day_label(older, today) not in ("today", "yesterday")


def test_a_selected_agent_keeps_its_chip_after_its_posts_scroll_away():
    # Only other agents' posts are in the feed, but the filter can still be cleared.
    page = views.feed_view([post(agent="hermes")], ["hermes"], {"codex"}, HANDLERS)
    tree = nodes(page)
    chips = {
        n.props["text"].split()[0]: n.props["role"]
        for n in tree.values()
        if n.props.get("role", "").startswith("chip")
    }
    assert chips == {"@hermes": "chip", "@codex": "chip-on"}


def test_the_strip_counts_today_per_agent_and_failures():
    noon = time.mktime(time.localtime()[:3] + (12, 0, 0, 0, 0, -1))
    posts = [
        post(id=3, agent="codex", status="failed", created_at=int(noon) - 60),
        post(id=2, agent="hermes", created_at=int(noon) - 120),
        post(id=1, agent="hermes", created_at=int(noon) - 2 * 86400),  # not today
    ]
    tree = nodes(views.feed_view(posts, None, set(), HANDLERS, now=noon))
    assert chip_texts(tree) == ["@codex 1", "@hermes 1"]
    assert tree["main.feed.strip.failed"].props["text"] == "✗ 1 failed today"


def test_a_sessions_posts_read_as_a_thread():
    posts = [
        post(id=3, session="s1", title="summary"),
        post(id=2, session="s1", kind="merge", title="merged"),
        post(id=1, session="s2", title="other session"),
    ]
    tree = nodes(views.feed_view(posts, None, set(), HANDLERS))
    assert "fb-cont" not in tree["main.feed.p3"].props["class"]
    assert "fb-cont" in tree["main.feed.p2"].props["class"]  # same session, right below
    assert "fb-cont" not in tree["main.feed.p1"].props["class"]
    # The hairline yields between a post and the continued one below it only.
    assert "fb-joined" in tree["main.feed.p3"].props["class"]
    assert "fb-joined" not in tree["main.feed.p2"].props["class"]  # the thread ends here
    assert "fb-joined" not in tree["main.feed.p1"].props["class"]
    assert gutter(tree["main.feed.p2"]).children == []  # the thread's own avatar ran out


def test_an_empty_diff_is_not_shown():
    tree = nodes(views.feed_view([post(additions=0, deletions=0)], ["hermes"], set(), HANDLERS))
    assert not any(n.props.get("role") in ("diff-add", "diff-del") for n in tree.values())
    assert "+0" not in views.summary(post(additions=0, deletions=0))


def test_counts_come_from_the_whole_store_when_given():
    page = views.feed_view([post()], ["hermes"], set(), HANDLERS, counts=(1, 750))
    assert nodes(page)["main.feed.strip.count"].props["text"] == "1/750"


def test_empty_feed_says_how_to_fill_it():
    tree = nodes(views.feed_view([], [], set(), HANDLERS))
    assert "post with" in tree["main.feed.empty"].props["text"]


def test_a_new_post_is_added_without_recreating_the_rail():
    first = views.feed_view([post()], ["hermes"], set(), HANDLERS)
    second = views.feed_view([post(id=9, title="a newer post"), post()], ["hermes"], set(), HANDLERS)
    ops = built(first).ops(built(second), "s1")
    adds = [op for op in ops if op[0] == "add"]
    dels = [op for op in ops if op[0] == "del"]
    assert not dels
    assert any(len(op) > 4 and "a newer post" in str(op[4]) for op in adds)
    assert {op[2] for op in adds} == {"main.feed"}


def test_agent_ordering_is_newest_first():
    posts = [post(id=9, agent="codex"), post(id=8, agent="hermes"), post(id=7, agent="omp")]
    page = views.feed_view(posts, None, set(), HANDLERS)
    tree = nodes(page)
    assert [text.split()[0] for text in chip_texts(tree)] == ["@codex", "@hermes", "@omp"]
