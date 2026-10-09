"""MCP behavior: the handshake, both tools, and a real server over pipes."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from feed_block import mcp_server
from feed_block.store import Store

PROTOCOL = mcp_server.PROTOCOL_VERSIONS[-1]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "feed.db") as s:
        yield s


def frame(method, params=None, rid=1):
    out = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        out["params"] = params
    if rid is not None:
        out["id"] = rid
    return out


def call(store, method, params=None, rid=1):
    reply = mcp_server.handle(frame(method, params, rid), store)
    assert reply is not None
    assert "error" not in reply, reply["error"]
    return reply["result"]


def tool(store, name, arguments=None, rid=1):
    reply = mcp_server.handle(frame("tools/call", {"name": name, "arguments": arguments}, rid), store)
    assert reply is not None
    return reply["result"]


def test_initialize_handshake(store):
    result = call(store, "initialize", {"protocolVersion": "2024-11-05", "clientInfo": {"name": "test"}})
    assert result["protocolVersion"] == "2024-11-05"  # the client's version is echoed
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["serverInfo"]["name"] == "feed-block"
    assert result["instructions"]


def test_initialize_unknown_version_falls_back(store):
    result = call(store, "initialize", {"protocolVersion": "1999-01-01"})
    assert result["protocolVersion"] == PROTOCOL


def test_tools_list_has_post_and_list(store):
    tools = call(store, "tools/list")["tools"]
    assert [t["name"] for t in tools] == ["post", "list"]
    post = tools[0]
    assert post["inputSchema"]["required"] == ["agent", "kind", "title"]
    assert set(post["inputSchema"]["properties"]) == {
        "agent",
        "kind",
        "title",
        "body",
        "repo",
        "link",
        "additions",
        "deletions",
        "status",
    }
    assert tools[1]["inputSchema"]["properties"]["limit"]["maximum"] == 500


def test_post_then_list_through_tools(store):
    receipt = tool(
        store,
        "post",
        {
            "agent": "hermes",
            "kind": "pr",
            "title": "pushed PR #42 to handover",
            "repo": "handover",
            "link": "https://github.com/x/handover/pull/42",
            "additions": 120,
            "deletions": 34,
        },
    )
    assert not receipt.get("isError")
    assert receipt["content"][0]["type"] == "text"
    assert "posted #1" in receipt["content"][0]["text"]
    assert "+120 −34" in receipt["content"][0]["text"]

    listed = json.loads(tool(store, "list")["content"][0]["text"])
    assert len(listed) == 1
    assert listed[0]["agent"] == "hermes"
    assert listed[0]["kind"] == "pr"
    assert listed[0]["additions"] == 120
    assert listed[0]["deletions"] == 34
    assert listed[0]["status"] == "ok"


def test_two_agents_post_and_list_shows_both(store):
    tool(store, "post", {"agent": "hermes", "kind": "commit", "title": "landed the dock"}, rid=1)
    tool(store, "post", {"agent": "codex", "kind": "test", "title": "42 passing"}, rid=2)
    assert [p["agent"] for p in json.loads(tool(store, "list")["content"][0]["text"])] == ["codex", "hermes"]
    hermes = json.loads(tool(store, "list", {"agent": "hermes"}, rid=3)["content"][0]["text"])
    assert [p["agent"] for p in hermes] == ["hermes"]


def test_post_defaults_are_optional(store):
    tool(store, "post", {"agent": "omp", "kind": "note", "title": "just a note"})
    posts = store.list()
    assert posts[0].body == "" and posts[0].status == "ok" and posts[0].additions is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "commit", "title": "no agent"},
        {"agent": "hermes", "title": "no kind"},
        {"agent": "hermes", "kind": "commit"},
        {"agent": "hermes", "kind": "tweet", "title": "not a kind"},
        {"agent": "hermes", "kind": "commit", "title": "bad status", "status": "maybe"},
        {"agent": "hermes", "kind": "commit", "title": "bad counts", "additions": -5},
        {"agent": 7, "kind": "commit", "title": "agent not a string"},
        {"agent": "hermes", "kind": "commit", "title": "  "},
    ],
)
def test_post_refuses_bad_arguments(store, arguments):
    result = tool(store, "post", arguments)
    assert result["isError"] is True
    assert result["content"][0]["type"] == "text"
    assert store.count() == 0


def test_unknown_tool_is_an_error_result(store):
    result = tool(store, "delete-everything", {})
    assert result["isError"] is True


def test_list_limit_and_agent_are_typed(store):
    assert json.loads(tool(store, "list", {"limit": 3})["content"][0]["text"]) == []
    assert tool(store, "list", {"limit": "lots"})["isError"] is True
    assert tool(store, "list", {"limit": True})["isError"] is True
    assert tool(store, "list", {"agent": 5})["isError"] is True


def test_ping_and_notifications(store):
    assert call(store, "ping") == {}
    assert mcp_server.handle(frame("notifications/initialized", rid=None), store) is None
    assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}, store) is None


def test_catalog_reads_answer_empty(store):
    assert call(store, "prompts/list") == {"prompts": []}
    assert call(store, "resources/list") == {"resources": []}


def test_unknown_method_is_a_protocol_error(store):
    reply = mcp_server.handle(frame("shutdown", rid=9), store)
    assert reply["error"]["code"] == mcp_server.METHOD_NOT_FOUND


def test_batch_frames_reply_in_order(store):
    batch = [
        frame("ping", rid="a"),
        frame("notifications/initialized", rid=None),
        frame(
            "tools/call",
            {"name": "post", "arguments": {"agent": "hermes", "kind": "note", "title": "b"}},
            rid="c",
        ),
    ]
    replies = mcp_server.handle_frames(batch, store)
    assert [r["id"] for r in replies] == ["a", "c"]
    assert store.count() == 1


def test_a_broken_frame_never_kills_the_server(store):
    reply = mcp_server.handle(["not", "an", "object"], store)
    assert reply["error"]["code"] == mcp_server.INVALID_REQUEST
    assert call(store, "ping") == {}


def test_serve_over_real_pipes_with_two_agents(tmp_path):
    """The done criterion, at the unit level: two agents post through MCP and both list."""
    env = {**os.environ, "FEED_BLOCK_DB": str(tmp_path / "feed.db"), "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "feed_block.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    try:

        def rpc(method, params=None, rid=1):
            process.stdin.write(json.dumps(frame(method, params, rid)) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
            assert line, "the server closed stdout"
            return json.loads(line)

        hello = rpc("initialize", {"protocolVersion": PROTOCOL}, rid=0)
        assert hello["result"]["serverInfo"]["name"] == "feed-block"
        process.stdin.write(json.dumps(frame("notifications/initialized", rid=None)) + "\n")
        process.stdin.flush()
        assert len(rpc("tools/list")["result"]["tools"]) == 2

        for rid, agent, title in (
            (1, "hermes", "pushed PR #42 to handover"),
            (2, "codex", "green build on main"),
        ):
            result = rpc(
                "tools/call",
                {
                    "name": "post",
                    "arguments": {
                        "agent": agent,
                        "kind": "pr" if rid == 1 else "test",
                        "title": title,
                        "repo": "handover",
                        "additions": 10,
                        "deletions": 2,
                    },
                },
                rid=rid,
            )["result"]
            assert not result.get("isError")

        listed = json.loads(rpc("tools/call", {"name": "list"}, rid=3)["result"]["content"][0]["text"])
        assert [p["agent"] for p in listed] == ["codex", "hermes"]
        assert {p["title"] for p in listed} == {"pushed PR #42 to handover", "green build on main"}
    finally:
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        assert process.stderr.read() == ""


def test_an_empty_batch_is_one_invalid_request(store):
    replies = mcp_server.handle_frames([], store)
    assert len(replies) == 1 and replies[0]["error"]["code"] == mcp_server.INVALID_REQUEST
