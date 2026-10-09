"""The timeline's nodes: the rail, the well, the strip, and the share summary."""

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


def content(node):
    return [c for c in node.children if c.props.get("role") == "content"][0]


def test_post_node_is_a_frameless_el_on_the_rail():
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


def test_head_line_is_handle_leader_time():
    node = views.post_node(post(), HANDLERS, now=time.time(), newest=False)
    head = [c for c in content(node).children if c.props.get("role") == "head"][0]
    roles = [c.props.get("role") or c.props.get("tag") for c in head.children]
    assert roles == ["handle", "leader", "time"]
    handle = head.children[0]
    assert handle.props["text"] == "@hermes"
    assert head.children[2].props["text"] == "2m"


def test_well_shows_repo_number_and_diff_stats():
    node = views.post_node(post(), HANDLERS, now=time.time(), newest=False)
    well = [c for c in content(node).children if c.props.get("role") == "well"][0]
    texts = {c.props.get("role"): c.props.get("text") for c in well.children}
    assert texts["well-repo"] == "handover"
    assert texts["well-number"] == "#42"
    assert texts["diff-add"] == "+120"
    assert texts["diff-del"] == "−34"
    assert texts["well-link"] == "https://github.com/x/handover/pull/42"


def test_well_is_skipped_without_anything_to_show():
    node = views.post_node(
        post(repo=None, link=None, additions=None, deletions=None), HANDLERS, now=time.time(), newest=False
    )
    assert not any(c.props.get("role") == "well" for c in node.children)


def test_body_is_one_muted_line():
    node = views.post_node(post(body="first\nsecond"), HANDLERS, now=time.time(), newest=False)
    body = [c for c in content(node).children if c.props.get("role") == "body"][0]
    assert body.props["text"] == "first"


def test_rail_column_carries_the_kind_glyph():
    node = views.post_node(post(kind="merge"), HANDLERS, now=time.time(), newest=False)
    rail = node.children[0]
    assert rail.props["role"] == "rail"
    assert rail.children[0].props["text"] == "✓"
    assert content(node) is node.children[1]


def test_failed_post_wears_the_failed_glyph():
    node = views.post_node(post(kind="commit", status="failed"), HANDLERS, now=time.time(), newest=False)
    assert node.children[0].children[0].props["text"] == "✗"


def test_actions_are_open_copy_share_and_open_needs_a_link():
    node = views.post_node(post(), HANDLERS, now=time.time(), newest=False)
    actions = [c for c in content(node).children if c.props.get("role") == "actions"][0]
    assert [c.props["text"] for c in actions.children] == ["open", "copy", "share"]

    bare = views.post_node(post(link=None), HANDLERS, now=time.time(), newest=False)
    actions = [c for c in content(bare).children if c.props.get("role") == "actions"][0]
    assert [c.props["text"] for c in actions.children] == ["copy", "share"]


def test_every_kind_has_a_glyph_role():
    for kind in ("commit", "pr", "merge", "test", "deploy", "note"):
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
    chips = [n for n in tree.values() if n.props.get("role", "").startswith("chip")]
    assert [c.props["text"] for c in chips] == ["@hermes", "@codex"]
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


def test_day_dividers_break_up_the_rail():
    today = int(time.time())
    older = today - 3 * 86400
    posts = [post(id=9, created_at=today), post(id=8, created_at=older, title="older work")]
    page = views.feed_view(posts, ["hermes"], set(), HANDLERS, now=float(today))
    tree = nodes(page)
    labels = sorted(n.props.get("text", "") for n in tree.values() if n.props.get("role") == "day-label")
    assert labels == sorted(["today", day_label(older, today)])
    assert day_label(older, today) not in ("today", "yesterday")


def test_a_selected_agent_keeps_its_chip_after_its_posts_scroll_away():
    # Only other agents' posts are on the rail, but the filter can still be cleared.
    page = views.feed_view([post(agent="hermes")], ["hermes"], {"codex"}, HANDLERS)
    tree = nodes(page)
    chips = {
        n.props["text"]: n.props["role"] for n in tree.values() if n.props.get("role", "").startswith("chip")
    }
    assert chips == {"@hermes": "chip", "@codex": "chip-on"}


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
    chips = [n for n in tree.values() if n.props.get("role", "").startswith("chip")]
    assert [c.props["text"] for c in chips] == ["@codex", "@hermes", "@omp"]
