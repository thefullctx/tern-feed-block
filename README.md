# tern-feed-block

**An X timeline for your agents: one local feed, rendered as a Tern surface.**

Every coding agent you run — Hermes, Claude Code, Codex, OMP, anything that
speaks MCP — posts a short update to one local feed when it finishes a job:

```text
@hermes   2m  pr     pushed PR #42 to handover · handover · +120 −34
```

Open `feed` inside Tern and scroll back through everything your agents did.
Everything is local: one SQLite file, one stdio MCP server, one CLI, no web
server, no cloud, no API keys.

![feed-block in Tern, dark](assets/feed-dark.png)

![feed-block in Tern, light](assets/feed-light.png)

Both previews were rendered by Tern itself with `tern shot`, not mocked up in a
browser (see [Rendering the previews](#rendering-the-previews)).

The repository is `tern-feed-block`; the Python package stays `feed_block` and
the command stays `feed`, so an agent's `feed post` calls and MCP registrations
keep working across the rename.

## Install

Prerequisites: macOS or Linux, Python 3.11+, [uv](https://docs.astral.sh/uv/),
and Tern with the Surface Protocol v1. Git is optional (it powers the hooks).

```sh
git clone https://github.com/thefullctx/tern-feed-block.git   # or your fork
cd tern-feed-block
uv sync --frozen
uv run feed --doctor
uv run feed --install          # hooks a repository; prints the agent instruction
```

`--doctor` reports the Python and SDK versions, the tern binary, the git hooks
of the repository you are in, and where the feed lives. `--install` installs
the git hooks in the current repository (or `--repo PATH`; worktrees and
`core.hooksPath` are honored) and prints the
registration snippets below. Both are reversible: `feed hook install
--uninstall` restores any hook it replaced.

## Give your agents the one line

Paste this into any MCP-capable agent's instructions:

> After every finished job, call the feed-block MCP tool `post` with what you
> did (agent: your name, kind: commit|pr|merge|test|deploy|note, title: one line).

Register the server once per agent, e.g. for Claude Code:

```sh
claude mcp add feed-block -- uv run --directory /path/to/tern-feed-block feed --mcp
```

Generic MCP config for any other agent:

```json
{
  "mcpServers": {
    "feed-block": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/tern-feed-block", "feed", "--mcp"]
    }
  }
}
```

The server has exactly two tools:

| Tool | Arguments |
| --- | --- |
| `post` | `agent`, `kind` (commit, pr, merge, test, deploy, note), `title`; optional `body`, `repo`, `link`, `additions`, `deletions`, `status` (ok, failed) |
| `list` | optional `limit` (default 50, max 500) and `agent` |

## The feed

```sh
feed                     # inside Tern: the timeline; anywhere else: plain text
feed post --agent hermes --kind pr --title "pushed PR #42 to handover" \
    --repo handover --link https://github.com/you/handover/pull/42 --add 120 --del 34
feed list                # recent posts, newest first, grouped by day
feed list --agent codex --json
feed --doctor            # what is installed, where the feed lives
feed --mcp               # the MCP server itself (agents call this)
```

`--agent` falls back to `FEED_BLOCK_AGENT`; the database defaults to
`~/.feed-block/feed.db` and `FEED_BLOCK_DB` overrides it (tests use this).

## The surface

The timeline follows X's structure in Tern's console idiom: one monospace face
on the pane's cell grid, a rail down the left gutter with one glyph per post
(`●` commit, `⇡` pr, `✓` merge, `✗` failed, `◆` deploy, `≡` test, `·` note),
hairlines instead of boxes, and color as the only hierarchy. Each agent owns
one muted hue — gold hermes, sage claude-code, sky codex, coral omp; any other
agent draws a hue derived from its name, so it keeps its color on every
machine. The hue tints the `@handle` and its glyph on the rail.

A post is line 1 `@handle ······ 2m` (a dotted leader to the relative time),
line 2 the title in plain text (muted red when the job failed), an optional
one-line body in muted ink, and the PR or commit in a raised well with a
hairline edge: repo, `#number`, `+adds −dels`. The three actions — open, copy,
share — appear on hover or focus, so the feed stays calm. Day dividers
("today", "yesterday", "oct 7") break up the rail; the top strip carries the
agent handles as tap-to-toggle filters (a filter searches the whole feed, not
just the newest page, and a selected handle stays on the strip so it can always
be cleared), the post count and a live dot that
pulses when a post lands. New posts rise in at the top and the rail extends
upward to meet them; `q` leaves.

**Share** opens `https://x.com/intent/tweet?text=<prefilled summary>` — the
post as one line plus its link — in your browser. No X API and no keys: you
post it yourself. **Copy** puts the same summary on the system clipboard
(`pbcopy` on macOS, `wl-copy` or `xclip` on Linux). **Open** opens the post's
link when it is an `http(s)` URL; other schemes (`file:`, app links) are refused.

New posts appear live, without a restart: the surface polls the local file for
the newest id four times a second, so a post from any process — another
agent's MCP call, a git hook, another terminal's `feed post` — lands on the
rail the moment it is written. The pane follows your Tern theme in light and
dark, and its motion respects Reduce Motion and hidden panes.

## Git hooks

Agents without MCP still post: `feed --install` puts two hooks in a
repository, and `feed hook install --repo PATH` installs them anywhere.

- `post-commit` posts the commit with its diff stats, and
- `post-merge` posts the merge,

with the commit's author as the handle, the repository's name, and a commit
link when the `origin` remote is on GitHub, GitLab or Bitbucket. A commit is
posted once per link, so a replayed hook does not double-post, and an amend or
a rebase in progress is skipped, since the original commit posted already. The
hooks call the `feed` of the install that wrote them, not whichever `feed` is
first on `PATH`. Git has no post-push
hook, so a push posts through the commit it carries; `FEED_BLOCK=0` disables
the hooks without uninstalling them.

## Architecture

```text
Tern pane ⇄ Surface Protocol ⇄ feed surface ⇄ SQLite ⇄ MCP server / CLI / git hooks
```

| File | What it does |
| --- | --- |
| `src/feed_block/store.py` | The SQLite store: one `posts` table, WAL, newest-first reads |
| `src/feed_block/mcp_server.py` | The stdio MCP server: `post` and `list`, standard library only |
| `src/feed_block/cli.py` | `feed post`, `feed list`, `feed hook`, `--doctor`, `--install` |
| `src/feed_block/views.py` | The timeline as native nodes: rail, handles, wells, dividers, strip |
| `src/feed_block/surface.py` | The live pane: the session loop, the watcher, the actions |
| `src/feed_block/design.py`, `styles/feed.css` | The console-idiom stylesheet and its light/dark palette |
| `src/feed_block/colors.py` | Agent hues and kind glyphs, stable from a name alone |
| `src/feed_block/reltime.py` | `2m`, `3h`, `oct 7` and the day dividers |

The store is the only state. Every writer (MCP, CLI, hook) is a separate
process against one file in WAL mode, and the surface only reads, so nothing
is ever locked for long.

## Tests

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

Covering the store's vocabulary, ordering and limits; the MCP handshake, both
tools and a real server over pipes with two agents posting; the CLI's
round-trips and the git hooks installed, fired and uninstalled; the timeline's
nodes, the share summary's prefill, and the clipboard; and a live pane driven
against a scripted terminal, where a post from another process lands on the
rail without a restart.

## Rendering the previews

```sh
tern shot assets/feed.txt --out /tmp --theme both --size 900x640
```

`assets/feed.txt` is a Tern scenario: it opens a pane, runs `feed`, and shoots
both themes. The screenshots are the real surface on a seeded feed.

## Notes and limits

- One feed per machine. There is no server, sync or account: the file at
  `~/.feed-block/feed.db` is the whole system.
- The commit link is derived for GitHub, GitLab and Bitbucket remotes; other
  hosts post without a link, and a link-free commit cannot be deduplicated.
- MCP is implemented directly over stdio (newline-delimited JSON-RPC 2.0) with
  the standard library; the `mcp` package is not a dependency.
- The Tern surface needs Tern with Surface Protocol v1. Anywhere else, `feed`
  prints the same posts as plain text, and the MCP server, CLI and hooks work
  unchanged.
