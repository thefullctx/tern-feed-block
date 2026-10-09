"""The feed's MCP server: newline-delimited JSON-RPC 2.0 on stdio, two tools.

Standard library only: `post` and `list`, so any MCP-capable agent (Hermes,
Claude Code, Codex, OMP, anything) can append to one local feed. stdout is
the protocol; diagnostics go to stderr.

Connect it with, e.g. for Claude Code:

    claude mcp add feed-block -- uv run --directory <repo> feed --mcp
"""

from __future__ import annotations

import functools
import json
import sys
import uuid
from collections.abc import Iterable
from typing import Any, IO

from . import __version__, agents
from .store import DEFAULT_LIMIT, KINDS, MAX_LIMIT, STATUSES, Post, Store

# MCP revisions this server answers; a client's version is echoed when known.
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
SERVER_INFO = {"name": "feed-block", "version": __version__}

POST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent": {
            "type": "string",
            "description": "Your agent's name, lower-case, e.g. 'hermes', 'claude-code', 'codex', 'omp'.",
        },
        "kind": {
            "type": "string",
            "enum": list(KINDS),
            "description": (
                "pr, merge or deploy for those results; session for the one summary of a session's work. "
                "commit, test and note exist, but single commits and passing tests are not worth a post."
            ),
        },
        "title": {
            "type": "string",
            "description": "One line, e.g. 'pushed PR #42 to handover'. The feed's rail shows this.",
        },
        "body": {"type": "string", "description": "Optional detail, shown muted under the title."},
        "repo": {"type": "string", "description": "Repository or project name, e.g. 'handover'."},
        "branch": {"type": "string", "description": "The branch the work is on."},
        "link": {"type": "string", "description": "URL of the PR, commit, run or page."},
        "additions": {"type": "integer", "minimum": 0, "description": "Lines added."},
        "deletions": {"type": "integer", "minimum": 0, "description": "Lines removed."},
        "status": {
            "type": "string",
            "enum": list(STATUSES),
            "description": "ok, or failed for a job that did not work out.",
        },
    },
    "required": ["agent", "kind", "title"],
}

LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_LIMIT,
            "description": f"Most posts to return, newest first (default {DEFAULT_LIMIT}).",
        },
        "agent": {"type": "string", "description": "Only this agent's posts."},
    },
}

TOOLS = [
    {
        "name": "post",
        "description": (
            "Post a result to the local feed-block feed, so the user can see what got done today. Only "
            "for results: a PR opened (kind pr), a merge (merge), a deploy (deploy), and one summary when "
            "you finish a session's work (session). Not for single commits or passing tests. agent is "
            "your own name, title is one line; body, repo, branch, link, additions, deletions and status "
            "(failed when it did not work out) are optional."
        ),
        "inputSchema": POST_SCHEMA,
    },
    {
        "name": "list",
        "description": (
            "List recent posts from the local feed, newest first. limit caps how many "
            f"(default {DEFAULT_LIMIT}, max {MAX_LIMIT}); agent shows one agent's posts only."
        ),
        "inputSchema": LIST_SCHEMA,
    },
]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@functools.cache
def process_session() -> str:
    """This server's session: the agent's own id when it exposes one, else one per process.

    A stdio server is started once per agent session, so its lifetime is the session's.
    """
    return agents.session() or uuid.uuid4().hex[:12]


class ToolError(Exception):
    """A tool failed on its input: an `isError` result, not a protocol error."""


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _string(arguments: dict, key: str, *, required: bool = False) -> str | None:
    value = arguments.get(key)
    if value is None:
        if required:
            raise ToolError(f"missing required argument: {key}")
        return None
    if not isinstance(value, str):
        raise ToolError(f"{key} must be a string")
    return value


def receipt(post: Post) -> str:
    stats = f" · {post.diffstat}" if post.diffstat else ""
    where = f" in {post.repo}" if post.repo else ""
    return f"posted #{post.id} to feed-block{where} · {post.agent} · {post.kind}{stats} · {post.status}"


def call_tool(name: str, arguments: Any, store: Store) -> str:
    """Runs one tool and returns its text content; raises ToolError on bad input."""
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        if name == "post":
            post = store.post(
                agent=_string(arguments, "agent", required=True) or "",
                kind=_string(arguments, "kind", required=True) or "",
                title=_string(arguments, "title", required=True) or "",
                body=_string(arguments, "body") or "",
                repo=_string(arguments, "repo"),
                link=_string(arguments, "link"),
                additions=arguments.get("additions"),
                deletions=arguments.get("deletions"),
                status=_string(arguments, "status") or "ok",
                branch=_string(arguments, "branch"),
                session=process_session(),
            )
            return receipt(post)
        if name == "list":
            limit = arguments.get("limit", DEFAULT_LIMIT)
            if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (int, float, str))):
                raise ToolError("limit must be an integer")
            posts = store.list(
                limit=DEFAULT_LIMIT if limit is None else limit, agent=_string(arguments, "agent")
            )
            return json.dumps([post.dict() for post in posts], ensure_ascii=False)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    raise ToolError(f"unknown tool: {name!r}")


def _dispatch(method: str, params: Any, store: Store) -> Any:
    if method == "initialize":
        version = PROTOCOL_VERSIONS[-1]
        if isinstance(params, dict):
            asked = params.get("protocolVersion")
            if isinstance(asked, str) and asked in PROTOCOL_VERSIONS:
                version = asked
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Call post for results only: a PR opened, a merge, a deploy, and one summary when you "
                "finish a session's work. Not for single commits or passing tests. Call list to read the feed."
            ),
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "tools/call takes an object")
        name = params.get("name")
        if not isinstance(name, str):
            raise RpcError(INVALID_PARAMS, "tools/call needs a tool name")
        try:
            text = call_tool(name, params.get("arguments"), store)
        except ToolError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        return {"content": [{"type": "text", "text": text}]}
    # Harmless catalog reads some clients send before they know the server.
    if method in ("prompts/list", "resources/list"):
        return {"prompts": []} if method.startswith("prompts") else {"resources": []}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    raise RpcError(METHOD_NOT_FOUND, f"method not found: {method}")


def handle(frame: Any, store: Store) -> dict | None:
    """Answers one JSON-RPC frame; `None` for notifications (no reply)."""
    if not isinstance(frame, dict):
        return _error(None, INVALID_REQUEST, "expected a JSON-RPC object")
    method = frame.get("method")
    rid = frame.get("id")
    if not isinstance(method, str):
        return _error(rid if isinstance(rid, (str, int)) else None, INVALID_REQUEST, "missing method")
    if rid is None or method.startswith("notifications/"):  # a notification never gets a reply
        return None
    try:
        result = _dispatch(method, frame.get("params"), store)
    except RpcError as exc:
        return _error(rid, exc.code, exc.message)
    except Exception as exc:  # a bug here must not kill the server
        print(f"feed-block MCP: {method} failed: {exc}", file=sys.stderr, flush=True)
        return _error(rid, INTERNAL_ERROR, str(exc))
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_frames(frames: Iterable[Any], store: Store) -> list[dict]:
    """Answers one JSON value: a frame, or a batch of frames (replies in order)."""
    if isinstance(frames, list):
        if not frames:  # JSON-RPC 2.0: an empty batch is one invalid request
            return [_error(None, INVALID_REQUEST, "empty batch")]
        return [reply for frame in frames if (reply := handle(frame, store)) is not None]
    reply = handle(frames, store)
    return [reply] if reply is not None else []


def serve(instream: IO[str], outstream: IO[str], store: Store | None = None) -> int:
    """Reads newline-delimited JSON-RPC from `instream`, writes replies to `outstream`."""
    own = store is None
    if own:
        store = Store()
    try:
        for line in instream:
            line = line.strip()
            if not line:
                continue
            try:
                frames = json.loads(line)
            except json.JSONDecodeError as exc:
                reply = _error(None, PARSE_ERROR, f"parse error: {exc.msg}")
                frames = None
            else:
                reply = None
            if frames is None:
                replies = [reply]
            else:
                replies = handle_frames(frames, store)
            for reply in replies:
                outstream.write(json.dumps(reply, ensure_ascii=False) + "\n")
                outstream.flush()
    finally:
        if own:
            store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    return serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
