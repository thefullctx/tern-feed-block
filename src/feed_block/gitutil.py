"""Git plumbing for the hooks and the automatic posts: read-only, never raising."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def run(args: list[str], cwd: Path) -> str | None:
    """A git command's trimmed stdout, or None when it fails or git is missing."""
    try:
        done = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def repo_root(cwd: Path | None = None) -> Path | None:
    top = run(["rev-parse", "--show-toplevel"], cwd or Path.cwd())
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
    out = run(["show", "--numstat", "--format=", "HEAD"], root)
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


def head(root: Path) -> str | None:
    return run(["rev-parse", "HEAD"], root)


def branch(root: Path) -> str | None:
    """The current branch, or None on a detached HEAD."""
    return run(["branch", "--show-current"], root) or None


def origin_commit_url(root: Path, sha: str | None) -> str | None:
    return commit_url(run(["config", "--get", "remote.origin.url"], root), sha or "")


def commits_since(root: Path, base: str | None) -> list[str]:
    """Subjects of the commits made since `base`, newest first."""
    if not base:
        return []
    out = run(["log", "--format=%s", f"{base}..HEAD"], root)
    return out.splitlines() if out else []


SHORTSTAT = re.compile(r"(?:(\d+) insertions?\(\+\))?(?:, )?(?:(\d+) deletions?\(-\))?$")


def changes_since(root: Path, base: str | None) -> tuple[int, int] | None:
    """Lines added and removed since `base`, committed or not; None when nothing changed."""
    out = run(["diff", "--shortstat", base or "HEAD"], root)
    if not out:
        return None
    found = SHORTSTAT.search(out.strip())
    adds = int(found.group(1) or 0) if found else 0
    dels = int(found.group(2) or 0) if found else 0
    return (adds, dels) if adds or dels else None
