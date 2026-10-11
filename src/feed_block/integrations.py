"""Wiring feed-block into the agents on this machine, and their events into autopost.

`feed --install` finds the agents installed for this user and, after asking,
sets each one up as far as it allows:

- Claude Code: hooks in `~/.claude/settings.json` (automatic posts),
- OMP and pi: an extension file in their extensions folder (automatic posts),
- Codex and Hermes: the MCP server in their config (the agent posts itself).

Every change carries a marker so `feed --install --uninstall` removes exactly
what was added, and a file is backed up once before its first change.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MARKER = "feed-block managed"
BLOCK_START = f"# {MARKER}: start"
BLOCK_END = f"# {MARKER}: end"
BACKUP_SUFFIX = ".before-feed-block"

# Claude Code events → (matcher, async). SessionEnd runs in the foreground: an
# async hook can be cut off as the session closes.
CLAUDE_EVENTS = {
    "SessionStart": (None, True),
    "PostToolUse": ("Bash", True),
    "PostToolUseFailure": ("Bash", True),
    "Stop": (None, True),
    "SessionEnd": (None, False),
}
# How much of a command's output reaches autopost: enough for a test summary line.
OUTPUT_TAIL = 4000


# -- Claude Code's events, in autopost's shape


def claude_event(payload: dict) -> dict | None:
    """One Claude Code hook payload as an autopost event, or None to ignore it."""
    name = payload.get("hook_event_name")
    event = {"agent": "claude-code", "session": payload.get("session_id"), "cwd": payload.get("cwd")}
    if name == "SessionStart":
        return {**event, "event": "start"}
    if name in ("PostToolUse", "PostToolUseFailure"):
        if payload.get("tool_name") != "Bash":
            return None
        command = (payload.get("tool_input") or {}).get("command")
        if not isinstance(command, str):
            return None
        if name == "PostToolUse":
            response = payload.get("tool_response")
            if isinstance(response, dict):
                output = "\n".join(str(response.get(key) or "") for key in ("stdout", "stderr"))
            else:
                output = str(response or "")
            return {
                **event,
                "event": "command",
                "command": command,
                "ok": True,
                "output": output[-OUTPUT_TAIL:],
            }
        error = str(payload.get("error") or "")
        return {**event, "event": "command", "command": command, "ok": False, "output": error[-OUTPUT_TAIL:]}
    if name == "Stop":
        return {**event, "event": "turn", "message": payload.get("last_assistant_message") or ""}
    if name == "SessionEnd":
        return {**event, "event": "end"}
    return None


# -- install plans


@dataclass
class Change:
    """One change to one agent's config, described before it is made."""

    agent: str
    path: Path
    what: str
    apply: Callable[[], None]
    automatic: bool  # posts on its own (hooks, extension), rather than through MCP


def feed_command(feed: str, python: str) -> list[str]:
    """How an agent runs this install's feed: its `feed`, or its interpreter."""
    return [feed] if feed else [python, "-m", "feed_block.cli"]


def plan(home: Path, command: list[str], *, uninstall: bool = False) -> tuple[list[Change], list[str]]:
    """The changes for every agent found under `home`, and notes for the ones left as they are."""
    changes: list[Change] = []
    notes: list[str] = []
    for step in (_claude, _extension("omp", ".omp"), _extension("pi", ".pi"), _codex, _hermes):
        change, note = step(home, command, uninstall)
        if change:
            changes.append(change)
        if note:
            notes.append(note)
    return changes, notes


# Where each agent lives, to tell which are installed for this user.
AGENT_HOMES = {
    "claude-code": ".claude",
    "omp": ".omp/agent",
    "pi": ".pi/agent",
    "codex": ".codex",
    "hermes": ".hermes/config.yaml",
}
AUTOMATIC = ("claude-code", "omp", "pi")


def status(home: Path) -> list[tuple[str, bool]]:
    """Each agent installed for this user, and whether feed-block is set up in it."""
    removable = {change.agent for change in plan(home, ["feed"], uninstall=True)[0]}
    return [(agent, agent in removable) for agent, where in AGENT_HOMES.items() if (home / where).exists()]


def _backup(path: Path) -> None:
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)


def _claude(home: Path, command: list[str], uninstall: bool) -> tuple[Change | None, str | None]:
    root = home / ".claude"
    if not root.is_dir():
        return None, None
    path = root / "settings.json"
    try:
        settings = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"claude-code: {path} is not readable JSON ({exc}); left as it is"
    if not isinstance(settings, dict):
        return None, f"claude-code: {path} is not a JSON object; left as it is"
    line = " ".join(shlex.quote(part) for part in command) + " agent-hook claude"
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    cleaned = {event: _without_ours(groups) for event, groups in hooks.items()}
    installed = cleaned != hooks
    if uninstall:
        if not installed:
            return None, None
        new = {event: groups for event, groups in cleaned.items() if groups}
        what = "remove the feed-block hooks"
    else:
        new = dict(cleaned)
        for event, (matcher, background) in CLAUDE_EVENTS.items():
            handler: dict = {"type": "command", "command": line}
            handler.update({"async": True} if background else {"timeout": 10})
            group: dict = {"hooks": [handler]}
            if matcher:
                group = {"matcher": matcher, **group}
            new[event] = [*new.get(event, []), group]
        if installed and new == hooks:
            return None, "claude-code: hooks already installed"
        what = f"add {len(CLAUDE_EVENTS)} hooks (session start/end, shell commands, end of each reply)"

    def apply() -> None:
        _backup(path)
        updated = dict(settings)
        if new:
            updated["hooks"] = new
        else:
            updated.pop("hooks", None)
        path.write_text(json.dumps(updated, indent=2) + "\n")

    return Change("claude-code", path, what, apply, automatic=True), None


def _without_ours(groups: object) -> list:
    """A hook event's matcher groups, minus the handlers feed-block added."""
    if not isinstance(groups, list):
        return []
    kept = []
    for group in groups:
        handlers = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(handlers, list):
            kept.append(group)
            continue
        others = [h for h in handlers if " agent-hook claude" not in str(h.get("command", ""))]
        if others:
            kept.append({**group, "hooks": others} if len(others) != len(handlers) else group)
    return kept


EXTENSION = """// {marker} extension: posts this agent's sessions to the local feed.
// Installed by `feed --install`; removed by `feed --install --uninstall`.
import {{ spawn }} from "node:child_process";
import path from "node:path";

const FEED = {command};
const AGENT = {agent};

export default function (pi) {{
  const commands = new Map();
  let session = "";
  let cwd = process.cwd();

  function track(ctx) {{
    try {{
      if (ctx?.cwd) cwd = ctx.cwd;
      const manager = ctx?.sessionManager;
      const file = manager?.getSessionFile?.();
      session = file ? path.basename(file, path.extname(file)) : manager?.getSessionId?.() || session;
    }} catch {{}}
  }}

  function send(event) {{
    if (!session) return;
    try {{
      const child = spawn(FEED[0], [...FEED.slice(1), "agent-hook", "event"], {{
        stdio: ["pipe", "ignore", "ignore"],
        detached: true,
      }});
      child.on("error", () => {{}});
      child.stdin.on("error", () => {{}});
      child.stdin.end(JSON.stringify({{ agent: AGENT, session, cwd, ...event }}));
      child.unref();
    }} catch {{}}
  }}

  function text(value) {{
    if (typeof value === "string") return value;
    const parts = Array.isArray(value?.content) ? value.content : Array.isArray(value) ? value : [];
    return parts.map((part) => (typeof part?.text === "string" ? part.text : "")).join("\\n");
  }}

  pi.on("session_start", (_event, ctx) => {{
    track(ctx);
    send({{ event: "start" }});
  }});

  pi.on("tool_execution_start", (event) => {{
    if (/^bash$/i.test(event?.toolName ?? "") && typeof event?.args?.command === "string") {{
      commands.set(event.toolCallId, event.args.command);
    }}
  }});

  pi.on("tool_execution_end", (event, ctx) => {{
    const command = commands.get(event?.toolCallId);
    if (command === undefined) return;
    commands.delete(event.toolCallId);
    track(ctx);
    send({{ event: "command", command, ok: !event.isError, output: text(event.result).slice(-{tail}) }});
  }});

  pi.on("agent_end", (event, ctx) => {{
    track(ctx);
    const replies = (event?.messages ?? []).filter((message) => message?.role === "assistant");
    send({{ event: "turn", message: text(replies[replies.length - 1]) }});
  }});

  pi.on("session_shutdown", (_event, ctx) => {{
    track(ctx);
    send({{ event: "end" }});
  }});
}}
"""


def _extension(agent: str, folder: str):
    def step(home: Path, command: list[str], uninstall: bool) -> tuple[Change | None, str | None]:
        root = home / folder / "agent"
        if not root.is_dir():
            return None, None
        path = root / "extensions" / "feed-block.ts"
        ours = path.exists() and MARKER in path.read_text()
        if path.exists() and not ours:
            return None, f"{agent}: {path} exists and is not feed-block's; left as it is"
        if uninstall:
            if not ours:
                return None, None
            return Change(agent, path, "remove the feed-block extension", path.unlink, automatic=True), None
        body = EXTENSION.format(
            marker=MARKER, command=json.dumps(command), agent=json.dumps(agent), tail=OUTPUT_TAIL
        )
        if ours and path.read_text() == body:
            return None, f"{agent}: extension already installed"

        def apply() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)

        return Change(agent, path, "add the feed-block extension", apply, automatic=True), None

    return step


def _block(text: str) -> str | None:
    """The feed-block managed block in a config file, if there is one."""
    found = re.search(rf"^{re.escape(BLOCK_START)}.*?^{re.escape(BLOCK_END)}\n?", text, re.S | re.M)
    return found.group(0) if found else None


def _config_block(
    agent: str, path: Path, block: str, uninstall: bool, taken: Callable[[str], bool], manual: str
) -> tuple[Change | None, str | None]:
    """Adds (or removes) a marked block at the end of a config file."""
    text = path.read_text() if path.exists() else ""
    current = _block(text)
    if uninstall:
        if not current:
            return None, None

        def remove() -> None:
            _backup(path)
            path.write_text(text.replace(current, "").rstrip("\n") + "\n")

        return Change(agent, path, "remove the feed-block MCP server", remove, automatic=False), None
    if current == block:
        return None, f"{agent}: MCP server already configured"
    if not current and taken(text):
        return None, f"{agent}: {path} already has its own MCP section; add feed-block yourself:\n{manual}"

    def apply() -> None:
        _backup(path)
        rest = text.replace(current, "") if current else text
        path.write_text(rest.rstrip("\n") + ("\n\n" if rest.strip() else "") + block)

    return Change(agent, path, "add the feed-block MCP server", apply, automatic=False), None


def _codex(home: Path, command: list[str], uninstall: bool) -> tuple[Change | None, str | None]:
    root = home / ".codex"
    if not root.is_dir():
        return None, None
    entry = (
        "[mcp_servers.feed-block]\n"
        f"command = {json.dumps(command[0])}\n"
        f"args = {json.dumps([*command[1:], '--mcp'])}\n"
    )
    block = f"{BLOCK_START}\n{entry}{BLOCK_END}\n"
    return _config_block(
        "codex", root / "config.toml", block, uninstall,
        lambda text: "[mcp_servers.feed-block]" in text, entry,
    )  # fmt: skip


def _hermes(home: Path, command: list[str], uninstall: bool) -> tuple[Change | None, str | None]:
    path = home / ".hermes" / "config.yaml"
    if not path.exists():
        return None, None
    entry = (
        "mcp_servers:\n"
        "  feed-block:\n"
        f"    command: {json.dumps(command[0])}\n"
        f"    args: {json.dumps([*command[1:], '--mcp'])}\n"
    )
    block = f"{BLOCK_START}\n{entry}{BLOCK_END}\n"
    # A second top-level `mcp_servers:` would replace the first, so only add one where none is.
    return _config_block(
        "hermes", path, block, uninstall,
        lambda text: re.search(r"^mcp_servers:", text, re.M) is not None, entry,
    )  # fmt: skip
