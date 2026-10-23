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


def _numstat_lines(out: str | None) -> tuple[int, int, bool]:
    """Summed `--numstat` output: additions, deletions, and whether any line counted."""
    adds = dels = 0
    seen = False
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue  # binary files report "-"
        adds += int(parts[0])
        dels += int(parts[1])
        seen = True
    return adds, dels, seen


def numstat(root: Path) -> tuple[int | None, int | None]:
    """Additions and deletions of HEAD's commit, when it has a diff to show."""
    adds, dels, seen = _numstat_lines(run(["show", "--numstat", "--format=", "HEAD"], root))
    return (adds, dels) if seen else (None, None)


def head(root: Path) -> str | None:
    return run(["rev-parse", "HEAD"], root)


def branch(root: Path) -> str | None:
    """The current branch, or None on a detached HEAD."""
    return run(["branch", "--show-current"], root) or None


def origin_commit_url(root: Path, sha: str | None) -> str | None:
    return commit_url(run(["config", "--get", "remote.origin.url"], root), sha or "")


# HEAD's reflog entries that make a commit of their own: committing, amending, cherry-picking,
# reverting, and the picks of a rebase (local commits replayed). Pulls, merges, checkouts and
# resets only bring in commits that already existed.
MADE = re.compile(
    r"^(?:commit|cherry-pick|revert"
    r"|(?:pull(?: --rebase)?|rebase(?: -i)?)[^:]*\((?:pick|reword|edit|squash|fixup|continue)\))"
)
REFLOG_TIME = re.compile(r"@\{(\d+)\}")


def _made_here(root: Path, since: int) -> set[str] | None:
    """Commits HEAD made itself from `since` on, by its reflog; None when there is no reflog."""
    out = run(["log", "-g", "--date=unix", "--format=%gd%x09%H%x09%gs", "HEAD"], root)
    if not out:
        return None
    made = set()
    for line in out.splitlines():
        parts = line.split("\t", 2)
        when = REFLOG_TIME.search(parts[0])
        if len(parts) < 3 or not when:
            continue
        # Not a `break`: entries take the committer's date, which need not run in order.
        if int(when.group(1)) >= since and MADE.match(parts[2]):
            made.add(parts[1])
    return made


def session_commits(root: Path, base: str | None, since: int) -> list[tuple[str, str]]:
    """(sha, subject) of the commits a session made since `base`, newest first.

    Commits pulled or merged in are in `base..HEAD` too, but not the session's
    work, so only those HEAD's reflog says were made here from `since` on count.
    Without a reflog, every commit since `base` does.
    """
    if not base:
        return []
    out = run(["log", "--format=%H%x09%s", f"{base}..HEAD"], root)
    listed = [
        (sha, subject) for sha, _, subject in (line.partition("\t") for line in (out or "").splitlines())
    ]
    made = _made_here(root, since)
    return listed if made is None else [(sha, subject) for sha, subject in listed if sha in made]


# Untracked files bigger than this, or past this many, are not read for line counts.
UNTRACKED_BYTES = 1_000_000
UNTRACKED_FILES = 500


def _untracked_lines(root: Path) -> int:
    """Lines in new files git does not track yet (ignored and binary files skipped)."""
    out = run(["ls-files", "--others", "--exclude-standard", "-z"], root)
    total = 0
    for name in (out or "").split("\0")[:UNTRACKED_FILES]:
        path = root / name
        try:
            if not name or not path.is_file() or path.stat().st_size > UNTRACKED_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue  # binary
        total += data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    return total


def session_changes(root: Path, commits: list[str]) -> tuple[int, int] | None:
    """Lines added and removed by these commits plus what is not committed yet (new files
    included); None when that is nothing. Merge commits add nothing of their own."""
    adds = dels = 0
    if commits:
        a, d, _ = _numstat_lines(run(["log", "--no-walk=unsorted", "--numstat", "--format=", *commits], root))
        adds, dels = adds + a, dels + d
    a, d, _ = _numstat_lines(run(["diff", "--numstat", "HEAD"], root))
    adds, dels = adds + a + _untracked_lines(root), dels + d
    return (adds, dels) if adds or dels else None
