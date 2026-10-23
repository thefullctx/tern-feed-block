"""Store behavior: the vocabulary, ordering, limits and cross-process reads."""

from __future__ import annotations

import pytest

from feed_block.store import DEFAULT_LIMIT, KINDS, MAX_LIMIT, STATUSES, Post, Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "feed.db") as s:
        yield s


def post(store, **kw):
    kw.setdefault("agent", "hermes")
    kw.setdefault("kind", "commit")
    kw.setdefault("title", "a post")
    return store.post(**kw)


def test_post_defaults_and_fields(store):
    made = post(
        store,
        agent="claude-code",
        kind="pr",
        title="opened #42",
        body="the body",
        repo="handover",
        link="https://github.com/x/y/pull/42",
        additions=12,
        deletions=3,
    )
    assert made.id == 1
    assert made.agent == "claude-code"
    assert made.kind == "pr"
    assert made.title == "opened #42"
    assert made.body == "the body"
    assert made.repo == "handover"
    assert made.link == "https://github.com/x/y/pull/42"
    assert made.additions == 12 and made.deletions == 3
    assert made.status == "ok" and not made.failed
    assert made.created_at > 0


def test_post_takes_the_first_line_of_a_title(store):
    assert post(store, title="one\ntwo\nthree").title == "one"


def test_every_kind_and_status_is_accepted(store):
    for index, kind in enumerate(KINDS):
        made = post(store, kind=kind, title=f"{kind} {index}")
        assert made.kind == kind
    for status in STATUSES:
        assert post(store, status=status, title=status).status == status


@pytest.mark.parametrize(
    "kw",
    [
        {"kind": "tweet"},
        {"kind": ""},
        {"status": "maybe"},
        {"agent": "  "},
        {"title": "   "},
        {"additions": -1},
        {"additions": "lots"},
        {"deletions": 1.5},
    ],
)
def test_post_refuses_bad_fields(store, kw):
    with pytest.raises(ValueError):
        post(store, **kw)


def test_blank_optional_fields_become_none(store):
    made = post(store, repo="  ", link="", body="")
    assert made.repo is None and made.link is None and made.body == ""


def test_list_is_newest_first(store):
    first, second, third = post(store, title="1"), post(store, title="2"), post(store, title="3")
    assert [p.id for p in store.list()] == [third.id, second.id, first.id]
    assert [p.title for p in store.list()] == ["3", "2", "1"]


def test_list_limit_and_clamping(store):
    for index in range(5):
        post(store, title=str(index))
    assert len(store.list(limit=2)) == 2
    assert len(store.list(limit=0)) == 1
    assert len(store.list(limit=MAX_LIMIT + 10_000)) == 5
    with pytest.raises(ValueError):
        store.list(limit="many")


def test_list_filters_by_agent(store):
    post(store, agent="hermes", title="h")
    post(store, agent="codex", title="c")
    assert [p.agent for p in store.list(agent="codex")] == ["codex"]
    assert len(store.list(agent="nobody")) == 0


def test_agents_lists_most_recent_first(store):
    post(store, agent="hermes")
    post(store, agent="codex")
    post(store, agent="hermes")
    assert store.agents() == ["hermes", "codex"]


def test_latest_id_count_and_has_link(store):
    assert store.latest_id() == 0
    assert store.count() == 0
    assert not store.has_link("https://example.com")
    post(store, link="https://example.com")
    assert store.latest_id() == 1
    assert store.count() == 1
    assert store.has_link("https://example.com")
    assert not store.has_link("https://other.example.com")


def test_empty_store_lists_nothing(store):
    assert store.list() == []


def test_posts_survive_reopening_and_other_processes_see_them(tmp_path):
    path = tmp_path / "feed.db"
    with Store(path) as writer:
        made = post(writer, title="from one process")
    with Store(path) as reader:
        assert [p.title for p in reader.list()] == [made.title]
        assert isinstance(reader.list()[0], Post)


def test_store_creates_its_directory(tmp_path):
    nested = tmp_path / "deep" / "nested" / "feed.db"
    with Store(nested) as store:
        post(store, title="nested")
        assert nested.exists()


def test_list_and_count_filter_by_several_agents(store):
    for agent in ("hermes", "codex", "omp", "codex"):
        store.post(agent=agent, kind="note", title=agent)
    assert [p.agent for p in store.list(agents={"codex", "omp"})] == ["codex", "omp", "codex"]
    assert store.count(agents=["codex"]) == 2
    assert store.count(agents=set()) == store.count() == 4  # no selection, no filter


def test_a_selected_agent_reaches_past_the_newest_page(store):
    store.post(agent="codex", kind="note", title="old work")
    for index in range(DEFAULT_LIMIT + 5):
        store.post(agent="hermes", kind="note", title=str(index))
    assert all(p.agent == "hermes" for p in store.list())
    assert [p.title for p in store.list(agents={"codex"})] == ["old work"]


def test_an_older_feed_gains_the_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, kind TEXT NOT NULL,"
        " title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', repo TEXT, link TEXT, additions INTEGER,"
        " deletions INTEGER, status TEXT NOT NULL DEFAULT 'ok', created_at INTEGER NOT NULL)"
    )
    db.execute("INSERT INTO posts (agent, kind, title, created_at) VALUES ('hermes', 'note', 'old', 1)")
    db.commit()
    db.close()
    with Store(path) as store:
        [old] = store.list()
        assert (old.title, old.branch, old.session) == ("old", None, None)
        made = store.post(agent="omp", kind="session", title="new", branch="main", session="s1")
        assert (made.branch, made.session) == ("main", "s1")


def test_list_and_count_narrow_by_kind_and_failure(tmp_path):
    with Store(tmp_path / "feed.db") as store:
        store.post(agent="omp", kind="pr", title="a")
        store.post(agent="omp", kind="deploy", title="b", status="failed")
        store.post(agent="codex", kind="deploy", title="c")
        assert [p.title for p in store.list(kind="deploy")] == ["c", "b"]
        assert [p.title for p in store.list(failed=True)] == ["b"]
        assert [p.title for p in store.list(agents={"codex"}, kind="deploy")] == ["c"]
        assert store.count(kind="deploy", failed=True) == 1
        assert store.kinds() == ["pr", "deploy"]  # the vocabulary's order, only kinds with posts
