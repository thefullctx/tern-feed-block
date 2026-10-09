"""The `feed` command: post, list, git hooks, and the Tern surface itself.

Bare `feed` opens the feed; inside Tern it hands the pane to the surface,
anywhere else it prints the recent posts as plain text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import sysconfig
from importlib.resources import files
from pathlib import Path

from . import __version__, agents
from .mcp_server import receipt
from .reltime import day_label, relative
from .store import DEFAULT_LIMIT, KINDS, STATUSES, Post, Store, default_db_path

HOOKS = ("post-commit", "post-merge")
HOOK_MARKER = "# feed-block managed hook"

# The instruction an agent needs, printed by --install.
AGENT_INSTRUCTION = (
    "After every finished job, call the feed-block MCP tool `post` with what you did "
    "(agent: your name, kind: commit|pr|merge|test|deploy|note, title: one line)."
)

# The kinds the surface's view needs from Tern's vocabulary.
SURFACE_KINDS = {"col", "row", "text", "el"}


# ---------------------------------------------------------------------------
# git plumbing (hooks only)


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        done = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def repo_root(cwd: Path | None = None) -> Path | None:
    top = _git(["rev-parse", "--show-toplevel"], cwd or Path.cwd())
    return Path(top) if top else None


# An origin remote on a known host: scp-style `git@host:path`, or an https/ssh URL
# with an optional user and port.
REMOTE = re.compile(
    r"^(?:(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/"
    r"|[^@/]+@(?P<scp>[^/:]+):)(?P<path>.+?)(?:\.git)?/?$"
)
# Where each host keeps a commit page.
COMMIT_PAGES = {
    "github.com": "https://github.com/{path}/commit/{sha}",
    "gitlab.com": "https://gitlab.com/{path}/-/commit/{sha}",
    "bitbucket.org": "https://bitbucket.org/{path}/commits/{sha}",
}


def commit_url(remote: str | None, sha: str) -> str | None:
    """A commit link from an origin remote on GitHub, GitLab or Bitbucket."""
    if not remote or not sha:
        return None
    found = REMOTE.match(remote.strip())
    if not found:
        return None
    page = COMMIT_PAGES.get((found["host"] or found["scp"]).lower())
    return page.format(path=found["path"], sha=sha) if page else None


def numstat(root: Path) -> tuple[int | None, int | None]:
    """Additions and deletions of HEAD's commit, when it has a diff to show."""
    out = _git(["show", "--numstat", "--format=", "HEAD"], root)
    if not out:
        return None, None
    adds = dels = 0
    seen = False
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue  # binary files report "-"
        adds += int(parts[0])
        dels += int(parts[1])
        seen = True
    return (adds, dels) if seen else (None, None)


def hook_context(root: Path) -> dict:
    """The commit a hook just saw: who, what, where, and how big."""
    return {
        "agent": agents.detect() or _git(["config", "--get", "user.name"], root) or "git",
        "title": _git(["log", "-1", "--format=%s"], root) or "a commit",
        "body": (_git(["log", "-1", "--format=%b"], root) or "").strip(),
        "repo": root.name,
        "link": commit_url(
            _git(["config", "--get", "remote.origin.url"], root), _git(["rev-parse", "HEAD"], root) or ""
        ),
        "additions": None,
        "deletions": None,
    }


def hooks_dir(repo: Path) -> Path | None:
    """Where git runs this repository's hooks: honors worktrees and core.hooksPath."""
    found = _git(["rev-parse", "--git-path", "hooks"], repo)
    return (repo / found).resolve() if found else None


def rewriting(root: Path) -> bool:
    """Whether HEAD's commit rewrites one already posted: an amend, or a rebase in progress."""
    for state in ("rebase-merge", "rebase-apply"):
        path = _git(["rev-parse", "--git-path", state], root)
        if path and (root / path).exists():
            return True
    action = _git(["reflog", "-1", "--format=%gs", "HEAD"], root) or ""
    return action.startswith("commit (amend)")


def run_hook(name: str, store: Store) -> int:
    """`feed hook run`: what a git hook calls. Idempotent per commit link."""
    if name not in HOOKS:
        print(f"feed: unknown hook {name!r}", file=sys.stderr)
        return 2
    root = repo_root()
    if root is None:
        return 0  # a hook fired outside a repository: nothing to post
    if name == "post-commit" and rewriting(root):
        return 0  # the original commit posted already; its rewrite would double-post
    info = hook_context(root)
    info["kind"] = "merge" if name == "post-merge" else "commit"
    if info["kind"] == "merge" and not info["title"].lower().startswith("merge"):
        info["title"] = f"merged {info['title']}"
    else:
        info["additions"], info["deletions"] = numstat(root)
    if info["link"] and store.has_link(info["link"]):
        return 0  # the same commit posted already (a rewritten or replayed hook)
    print(receipt(store.post(**info)))
    return 0


# ---------------------------------------------------------------------------
# hook installation


def _hooks_dir() -> Path:
    return Path(str(files("feed_block").joinpath("hooks")))


def feed_bin() -> str:
    """The command the hooks should call: this install's `feed`, or its interpreter."""
    # This interpreter's own scripts directory: not resolved, since a venv's python is a
    # symlink to its base interpreter and resolving it would find another install's feed.
    for scripts in (sysconfig.get_path("scripts"), str(Path(sys.executable).parent)):
        beside = Path(scripts) / "feed"
        if beside.is_file() and os.access(beside, os.X_OK):
            return str(beside)
    found = shutil.which("feed")
    return str(Path(found).resolve()) if found else ""


def hook_body(name: str) -> str:
    """A hook script with this installation's feed command baked in."""
    body = _hooks_dir().joinpath(name).read_text()
    return body.replace("@FEED_BIN@", feed_bin()).replace("@PYTHON@", sys.executable)


def project_dir() -> Path | None:
    """The feed-block checkout, when this install runs from one."""
    here = Path(__file__).resolve().parent.parent
    return here if (here / "pyproject.toml").exists() else None


def install_hooks(repo: Path, *, uninstall: bool = False) -> list[str]:
    """Installs (or restores) feed-block's hooks in one repository, reversibly."""
    hooks = hooks_dir(repo)
    if hooks is None:
        raise RuntimeError(f"not a git repository: {repo}")
    hooks.mkdir(parents=True, exist_ok=True)
    done = []
    for name in HOOKS:
        target = hooks / name
        backup = target.with_name(f"{name}.before-feed-block")
        if uninstall:
            if backup.exists():
                backup.replace(target)
                done.append(f"restored {name} from its backup")
            elif target.is_file() and HOOK_MARKER in target.read_text():
                target.unlink()
                done.append(f"removed {name}")
            else:
                done.append(f"{name}: nothing installed")
            continue
        kept = False
        if target.exists() and HOOK_MARKER not in target.read_text():
            shutil.copy2(target, backup)
            kept = True
        target.write_text(hook_body(name))
        target.chmod(0o755)
        done.append(f"installed {name}" + (" (kept a .before-feed-block backup)" if kept else ""))
    return done


# ---------------------------------------------------------------------------
# plain output


def plain_post(post: Post, width: int) -> str:
    """One post as aligned columns: handle, age, kind (✗ when it failed), then the title."""
    mark = "✗" if post.failed else " "
    extras = [part for part in (post.repo, post.diffstat) if part]
    title = " · ".join([post.title, *extras])
    line = f"{'@' + post.agent:<{width}}  {relative(post.created_at):>4}  {mark} {post.kind:<6}  {title}"
    detail = post.link or post.body.replace("\n", " ⏎ ")
    # The detail sits under the title, so the columns read straight down.
    indent = " " * (width + 2 + 4 + 2 + 2 + 6 + 2)
    return f"{line}\n{indent}{detail}" if detail else line


def plain_feed(posts: list[Post], store: Store | None = None) -> str:
    """Recent posts as console text: the feed without Tern."""
    if not posts:
        return "feed-block: the feed is empty. Post with: feed post --agent you --kind note --title …"
    where = f" · {store.count()} posts · {store.path}" if store else ""
    blocks = [f"feed-block{where}"]
    width = max(len(post.agent) + 1 for post in posts)
    days: dict[str, list[Post]] = {}
    for post in posts:
        days.setdefault(day_label(post.created_at), []).append(post)
    for label, day in days.items():
        blocks.append(f"── {label} " + "─" * max(0, 58 - len(label)))
        blocks.extend(plain_post(post, width) for post in day)
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# surfaces


def open_feed() -> int:
    """Bare `feed`: the Tern surface inside Tern, plain text anywhere else."""
    try:
        import tern_sdk
    except ImportError:
        tern_sdk = None  # type: ignore[assignment]
    store = Store()
    try:
        session = tern_sdk.connect(app="feed-block", version=__version__) if tern_sdk else None
        if session is None or not SURFACE_KINDS.issubset(session.caps.kinds):
            if session is not None:
                session.close()
            print(plain_feed(store.list(), store))
            return 0
        from .surface import Feed

        return Feed(session, store).run()
    finally:
        store.close()


# ---------------------------------------------------------------------------
# commands


def cmd_post(args: argparse.Namespace) -> int:
    with Store() as store:
        made = store.post(
            agent=args.agent or agents.detect() or "",
            kind=args.kind,
            title=args.title,
            body=args.body,
            repo=args.repo,
            link=args.link,
            additions=args.additions,
            deletions=args.deletions,
            status=args.status,
        )
    print(receipt(made))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    with Store() as store:
        posts = store.list(limit=args.limit, agent=args.agent)
        if args.json:
            print(json.dumps([post.dict() for post in posts], ensure_ascii=False, indent=2))
        elif args.agent:
            print(plain_feed(posts) if posts else f"feed-block: no posts from {args.agent}")
        else:
            print(plain_feed(posts, store))
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    if args.hook_cmd == "install":
        repo = Path(args.repo).resolve() if args.repo else repo_root()
        if repo is None:
            print("feed: not a git repository; pass --repo PATH", file=sys.stderr)
            return 2
        for line in install_hooks(repo, uninstall=args.uninstall):
            print(line)
        verb = "Uninstalled from" if args.uninstall else "Installed in"
        print(f"{verb} {repo}. Commits and merges now post to the feed.")
        return 0
    if args.hook_cmd == "run":
        with Store() as store:
            return run_hook(args.name, store)
    print("feed: hook needs install or run", file=sys.stderr)
    return 2


def doctor() -> int:
    """`feed --doctor`: what is installed, what is missing, where the feed lives."""
    try:
        import tern_sdk

        sdk = (True, f"tern-sdk {tern_sdk.wire.VERSION} (Surface Protocol)")
    except ImportError:
        sdk = (False, "tern-sdk missing (the Tern surface needs it)")
    hooks = "not in a repository"
    root = repo_root()
    if root is not None:
        where = hooks_dir(root)
        found = [
            name
            for name in HOOKS
            if where and (where / name).is_file() and HOOK_MARKER in (where / name).read_text()
        ]
        hooks = f"{', '.join(found) or 'not installed'} ({root.name})"
    tern = shutil.which("tern")
    report("ok" if sys.version_info >= (3, 11) else "MISS", f"python {sys.version.split()[0]}")
    report("ok" if sdk[0] else "MISS", sdk[1])
    report("info", hooks)
    report(
        "ok" if tern else "info",
        f"tern at {tern}" if tern else "tern not on PATH (needed to view the surface)",
    )
    try:
        with Store(default_db_path()) as store:
            report("ok", f"feed {store.path} · {store.count()} posts")
            return 0
    except Exception as exc:  # a broken feed file is the one failure that matters
        report("MISS", f"feed {default_db_path()} · {exc}")
        return 1


def report(status: str, text: str) -> None:
    """One `--doctor` line: the status in a fixed column, then what it is about."""
    print(f"{status:<5}{text}")


def install(args: list[str]) -> int:
    """`feed --install [--repo PATH]`: git hooks, plus the agent instructions."""
    parser = argparse.ArgumentParser(prog="feed --install")
    parser.add_argument("--repo", help="Repository to hook (default: the current one)")
    parsed = parser.parse_args(args)
    repo = Path(parsed.repo).resolve() if parsed.repo else repo_root()
    if repo is None:
        print("feed: not a git repository; pass --repo PATH", file=sys.stderr)
        return 2
    for line in install_hooks(repo):
        print(line)
    print()
    print("Give your agent this one line:")
    print(f"  {AGENT_INSTRUCTION}")
    print()
    checkout = project_dir()
    run_from = ["--directory", str(checkout)] if checkout else []
    print("Or register the MCP server yourself, e.g. for Claude Code:")
    print("  claude mcp add feed-block -- uv run " + " ".join(run_from + ["feed", "--mcp"]))
    print("Generic MCP config (any MCP-capable agent):")
    args = ["run", *run_from, "feed", "--mcp"] if checkout else ["feed", "--mcp"]
    print(
        json.dumps(
            {"mcpServers": {"feed-block": {"command": "uv" if checkout else "feed", "args": args}}}, indent=2
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feed", description="A local, agent-agnostic activity feed.")
    sub = parser.add_subparsers(dest="cmd")

    post = sub.add_parser("post", help="post a finished job to the feed")
    post.add_argument("--agent", help="your agent's name (or FEED_BLOCK_AGENT)")
    post.add_argument("--kind", required=True, choices=KINDS)
    post.add_argument("--title", required=True, help="one line, e.g. 'pushed PR #42 to handover'")
    post.add_argument("--body", default="")
    post.add_argument("--repo")
    post.add_argument("--link")
    post.add_argument("--add", "--additions", dest="additions", type=int)
    post.add_argument("--del", "--deletions", dest="deletions", type=int)
    post.add_argument("--status", choices=STATUSES, default="ok")

    listing = sub.add_parser("list", help="show recent posts")
    listing.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    listing.add_argument("--agent")
    listing.add_argument("--json", action="store_true")

    hook = sub.add_parser("hook", help="git hooks that post commits and merges")
    hooks = hook.add_subparsers(dest="hook_cmd")
    hook_install = hooks.add_parser("install", help="install the hooks in a repository")
    hook_install.add_argument("--repo")
    hook_install.add_argument("--uninstall", action="store_true")
    hook_run = hooks.add_parser("run", help="called by the hook itself")
    hook_run.add_argument("--name", required=True, choices=HOOKS)
    hook_run.add_argument("rest", nargs="*")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args[:1] in (["--doctor"], ["-doctor"], ["doctor"]):
            return doctor()
        if args[:1] in (["--install"], ["-install"]):
            return install(args[1:])
        if args[:1] in (["--mcp"], ["-mcp"], ["mcp"]):
            from .mcp_server import main as mcp_main

            return mcp_main()
        if args[:1] in (["--version"], ["-V"], ["version"]):
            print(f"feed {__version__}")
            return 0
        parsed = build_parser().parse_args(args)
        if parsed.cmd == "post":
            return cmd_post(parsed)
        if parsed.cmd == "list":
            return cmd_list(parsed)
        if parsed.cmd == "hook":
            return cmd_hook(parsed)
        return open_feed()
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"feed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
