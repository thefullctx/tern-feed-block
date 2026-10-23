"""Automatic posts: agent events in, at most a few posts per session out.

Every agent integration (Claude Code's hooks, the OMP and pi extension) turns
its own events into one shape and hands them here:

    {"agent": "claude-code", "session": "…", "cwd": "/repo", "event": "start"}
    {"…", "event": "command", "command": "pytest -q", "ok": false, "output": "…"}
    {"…", "event": "turn", "message": "Added offline mode."}    # the agent finished a reply
    {"…", "event": "end", "message": "…"}                       # the session closed

Only results are posted, never activity:

- one summary per session and repository, replaced (and moved to the top) as
  the session goes on, and only once something changed; it turns failed when
  the session's last test run is red,
- a PR opened, a PR merged, a deploy.

Commits and test runs are counted into the summary instead of posted.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from . import gitutil
from .config import Config
from .store import Store

# The longest title a summary keeps, in characters.
TITLE_MAX = 100

PR_URL = re.compile(r"https://\S+?/pull/(\d+)")
# `gh pr merge` says: "✓ Squashed and merged pull request owner/repo#42 (Add offline mode)".
MERGED = re.compile(r"[Mm]erged pull request \S*?#(\d+) \((.+)\)")
# The failure count a test runner prints, in the shapes pytest, jest, cargo and go use; the
# last one wins, since runners end on their total (jest prints suites, then tests). "3 failed
# to compile" is a build log talking, not a count.
FAILURES = re.compile(r"\b(\d+) (?:failed|failing|failures?)\b(?! to\b)")
# Wrappers that run the real command: `uv run pytest`, `npx vitest`, `time cargo test`.
WRAPPERS = ("uv run", "poetry run", "pipenv run", "pdm run", "hatch run", "npx", "pnpm exec", "bunx", "time")


def handle(event: dict, store: Store, config: Config) -> list[str]:
    """Applies one event; returns a line per post written (for logs and tests)."""
    session = str(event.get("session") or "").strip()
    agent = str(event.get("agent") or "").strip()
    cwd = Path(str(event.get("cwd") or "."))
    kind = event.get("event")
    if not session or not agent:
        return []
    root = gitutil.repo_root(cwd)
    if kind == "start":
        if root is not None:
            store.open_session(session, str(root), agent, gitutil.head(root))
        return []
    if kind == "command":
        return _command(event, agent, session, root, store, config)
    if kind in ("turn", "end"):
        return _summaries(event, agent, session, store, config)
    return []


# -- commands


# What separates two simple commands in a shell line.
SEPARATORS = ("&&", "||", ";", "|", "\n")


def split_commands(command: str) -> list[str]:
    """A shell line split at `&&`, `||`, `;`, `|` and newlines outside quotes.

    `git commit -m "fix; fly deploy"` is one command. A line whose quotes never
    close (an apostrophe in a heredoc body) is split everywhere instead, as text.
    """
    parts, current, quote, i = [], [], "", 0
    while i < len(command):
        char = command[i]
        if char == "\\" and quote != "'" and i + 1 < len(command):
            current.append(command[i : i + 2])
            i += 2
            continue
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        else:
            separator = next((s for s in SEPARATORS if command.startswith(s, i)), None)
            if separator:
                parts.append("".join(current))
                current = []
                i += len(separator)
                continue
        current.append(char)
        i += 1
    if quote:
        return re.split(r"&&|\|\||;|\||\n", command)
    return [*parts, "".join(current)]


def segments(command: str) -> list[str]:
    """The simple commands in a shell line, each without leading `VAR=x` or wrappers."""
    found = []
    for part in split_commands(command):
        words = part.strip().split()
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]
        text = " ".join(words)
        changed = True
        while changed:
            changed = False
            for wrapper in WRAPPERS:
                if text == wrapper or text.startswith(wrapper + " "):
                    text = text[len(wrapper) :].strip()
                    changed = True
        if text:
            found.append(text)
    return found


def matches(command: str, patterns: tuple[str, ...]) -> str | None:
    """The first pattern one of the command's segments starts with, word for word."""
    for text in segments(command):
        base = text.split()[0].rsplit("/", 1)[-1] + text[len(text.split()[0]) :]  # .venv/bin/pytest
        for pattern in patterns:
            if base == pattern or base.startswith(pattern + " "):
                return pattern
    return None


def _command(
    event: dict, agent: str, session: str, root: Path | None, store: Store, config: Config
) -> list[str]:
    command = str(event.get("command") or "")
    ok = bool(event.get("ok", True))
    output = str(event.get("output") or "")
    if root is not None:
        store.open_session(session, str(root), agent, gitutil.head(root))
    repo = root.name if root else None
    branch = gitutil.branch(root) if root else None
    common = {"agent": agent, "session": session, "repo": repo, "branch": branch}

    test = matches(command, config.test_commands)
    if test and root is not None:
        # The runner's own count wins over the exit code: `pytest | tail` exits 0 when tests fail.
        counts = FAILURES.findall(output)
        failed = int(counts[-1]) if counts else 0
        if failed:
            ok = False
        summary = f"{failed} failed" if failed else ("passing" if ok else "failing")
        store.update_session(session, str(root), tests_ok=int(ok), tests_summary=f"{test} · {summary}")
        return []

    words = _words(command)
    if ok and _has(words, "gh", "pr", "create") and config.posts_enabled("pr"):
        url = PR_URL.search(output)
        if url and store.has_link(url.group(0)):
            return []
        number = f"PR #{url.group(1)}" if url else "a PR"
        title = pr_title(command)
        made = store.post(
            kind="pr", title=f"opened {number}" + (f" {title}" if title else ""),
            link=url.group(0) if url else None, **common,
        )  # fmt: skip
        return [f"pr #{made.id}"]

    if ok and _has(words, "gh", "pr", "merge") and config.posts_enabled("merge"):
        merged = MERGED.search(output)
        title = f"merged PR #{merged.group(1)} {merged.group(2)}" if merged else "merged a PR"
        url = PR_URL.search(command + " " + output)
        made = store.post(kind="merge", title=title, link=url.group(0) if url else None, **common)
        return [f"merge #{made.id}"]

    deploy = matches(command, config.deploy_commands)
    if deploy and config.posts_enabled("deploy"):
        target = f"deployed {repo}" if repo else "deployed"
        title = f"{target} with {deploy.split()[0]}" if ok else f"deploy failed: {deploy}"
        made = store.post(
            kind="deploy", title=title, status="ok" if ok else "failed",
            body="" if ok else _last_line(output), **common,
        )  # fmt: skip
        return [f"deploy #{made.id}"]
    return []


def _words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _has(words: list[str], *run: str) -> bool:
    """Whether the words contain this run of words, in order and adjacent."""
    return any(tuple(words[i : i + len(run)]) == run for i in range(len(words)))


# `--title "…"`, `-t '…'` or `--title=word`, read straight from the command line: a heredoc
# body with an apostrophe in it ("Hermes's") makes the whole line unparseable as shell words.
TITLE_FLAG = re.compile(
    r"""(?:^|\s)(?:--title|-t)(?:=|\s+)(?:"((?:[^"\\\n]|\\.)*)"|'([^'\n]*)'|([^\s"'$][^\s]*))"""
)


def pr_title(command: str) -> str | None:
    """The title a `gh pr create` command gives, when it is written out literally."""
    found = TITLE_FLAG.search(command)
    if not found:
        return None
    double, single, bare = found.groups()
    if double is not None:
        if re.search(r"\$[({]|`", double):
            return None  # the shell computes it; the literal text would be noise
        return re.sub(r"\\(.)", r"\1", double).strip() or None
    return (single if single is not None else bare).strip() or None


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:TITLE_MAX] if lines else ""


# -- session summaries


def _summaries(event: dict, agent: str, session: str, store: Store, config: Config) -> list[str]:
    if not config.posts_enabled("session"):
        return []
    message = headline(str(event.get("message") or ""))
    written = []
    for state in store.session_roots(session):
        root = Path(state["root"])
        if not root.is_dir():
            continue
        made = gitutil.session_commits(root, state["base"], state["started_at"])
        commits = [subject for _, subject in made]
        changes = gitutil.session_changes(root, [sha for sha, _ in made])
        if not commits and not changes:
            continue  # nothing changed here: nothing to say
        failed = state["tests_ok"] == 0
        previous = store.get(state["post_id"]) if state["post_id"] else None
        # An event without a reply (the session closing) keeps the title the last reply gave.
        title = (
            message
            or (previous.title if previous else "")
            or (commits[0] if commits else f"worked on {root.name}")
        )
        details = []
        if failed:
            details.append(f"tests still failing · {state['tests_summary']}")
        if commits:
            details.append(f"{len(commits)} commit{'s' if len(commits) != 1 else ''}")
        if not commits and changes:
            details.append("uncommitted")
        adds, dels = changes or (None, None)
        fields = dict(
            title=title, body=" · ".join(details), branch=gitutil.branch(root), additions=adds,
            deletions=dels, status="failed" if failed else "ok",
        )  # fmt: skip
        work = {name: value for name, value in fields.items() if name != "title"}
        if previous and all(getattr(previous, name) == value for name, value in work.items()):
            # No new work: a reply that only talks (answers a question, says goodbye)
            # neither retitles the summary nor bumps it to the top.
            continue
        made = store.replace_session_post(
            session, str(root), agent=agent, kind="session", repo=root.name, **fields
        )
        written.append(f"session #{made.id}")
    return written


def headline(message: str) -> str:
    """The first statement of an agent's reply, without markdown, as a one-line title.

    Headings ("# Summary") say little, so prose wins over them; questions to the
    user ("Want me to update the docs?") say nothing about the work, so they are
    skipped. A reply with no statement gives "".
    """
    lines = message.splitlines()
    prose = [line for line in lines if not line.lstrip().startswith("#")]
    for line in prose + lines:
        text = re.sub(r"^\s*(?:[#>*+-]+|\d+[.)])\s*", "", line)  # headings, quotes, list markers
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [label](link) → label
        text = re.sub(r"[*_`]+", "", text).strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            sentence = sentence.strip().rstrip(":")
            if not sentence or sentence.endswith("?"):
                continue
            return sentence if len(sentence) <= TITLE_MAX else sentence[: TITLE_MAX - 1].rstrip() + "…"
    return ""
