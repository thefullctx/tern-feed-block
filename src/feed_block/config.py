"""The feed's settings: `~/.feed-block/config.toml`, every key optional.

```toml
[git]
commits = false          # post every commit from the git hooks, not just merges

[posts]                  # switch any automatic post off
session = true
pr = true
merge = true
deploy = true

[commands]               # added to the built-in lists, matched at a command's start
test = ["make check"]
deploy = ["./scripts/ship"]
```
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .store import default_db_path

# Commands that run a test suite, after any `uv run`, `npx` or `VAR=x` prefix.
TEST_COMMANDS = (
    "pytest", "python -m pytest", "python3 -m pytest", "tox", "nox",
    "npm test", "npm run test", "pnpm test", "pnpm run test", "yarn test", "bun test", "deno test",
    "vitest", "jest", "cargo test", "cargo nextest", "go test", "make test", "rspec", "mix test",
    "dotnet test", "swift test", "gradle test", "./gradlew test", "mvn test",
)  # fmt: skip
# Commands that ship something.
DEPLOY_COMMANDS = (
    "fly deploy", "flyctl deploy", "vercel --prod", "vercel deploy", "netlify deploy",
    "wrangler deploy", "wrangler pages deploy", "railway up", "firebase deploy",
    "gcloud app deploy", "gcloud run deploy", "cdk deploy", "serverless deploy", "sls deploy",
    "kamal deploy", "make deploy",
)  # fmt: skip


@dataclass(frozen=True)
class Config:
    git_commits: bool = False
    posts: dict[str, bool] = field(
        default_factory=lambda: {"session": True, "pr": True, "merge": True, "deploy": True}
    )
    test_commands: tuple[str, ...] = TEST_COMMANDS
    deploy_commands: tuple[str, ...] = DEPLOY_COMMANDS

    def posts_enabled(self, kind: str) -> bool:
        return self.posts.get(kind, True)


def config_path() -> Path:
    """`$FEED_BLOCK_CONFIG`, else `config.toml` beside the feed."""
    override = os.environ.get("FEED_BLOCK_CONFIG")
    return Path(override).expanduser() if override else default_db_path().parent / "config.toml"


def load(path: Path | None = None) -> Config:
    """The settings, with defaults for anything missing; a broken file is reported and ignored."""
    path = config_path() if path is None else path
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return Config()
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"feed: ignoring {path}: {exc}", file=sys.stderr)
        return Config()
    base = Config()
    posts = dict(base.posts)
    posts.update({k: bool(v) for k, v in (data.get("posts") or {}).items()})
    commands = data.get("commands") or {}
    return Config(
        git_commits=bool((data.get("git") or {}).get("commits", False)),
        posts=posts,
        test_commands=base.test_commands + tuple(str(c) for c in commands.get("test", [])),
        deploy_commands=base.deploy_commands + tuple(str(c) for c in commands.get("deploy", [])),
    )
