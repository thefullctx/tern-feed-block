# tern-feed-block

**An X timeline for your agents: one local feed, rendered as a Tern surface.**

Every coding agent you run — Claude Code, OMP, pi, Codex, Hermes, anything
that speaks MCP — shows up on one local feed, with results only: what got
done, and what needs you.

```text
╭──╮
│✳│◇ Claude Code  @claude-code · 5m · session   ↗ open  ⧉ copy  ⤴ share
╰──╯  wired the queue into the sync worker
      ┌────────────────────────────────────────┐
      │ handover · feature/retry       d1eeb2a │
      │ ■■■■■■■■■■  +120 −34                    │
      └────────────────────────────────────────┘
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
uv tool install --editable .   # the `feed` command, for every project
feed --doctor
feed --install                 # sets up the agents it finds, after asking
```

`--doctor` reports the Python and SDK versions, the tern binary, the git hooks
of the repository you are in, each agent installed for you and whether it is
set up (and how it posts), and where the feed lives.

`feed --install` looks for the agents installed for your user, shows what it
will change for each, and asks before changing anything (`--yes` skips the
question). It is one install for all your projects:

| Agent | What `--install` adds | How it posts |
| --- | --- | --- |
| Claude Code | five hooks in `~/.claude/settings.json` | automatically |
| OMP, pi | `extensions/feed-block.ts` in `~/.omp/agent` or `~/.pi/agent` | automatically |
| Codex | the MCP server in `~/.codex/config.toml` | the agent calls `post` |
| Hermes | the MCP server in `~/.hermes/config.yaml` | the agent calls `post` |

Every file is backed up once (`<file>.before-feed-block`) before its first
change, and `feed --install --uninstall` removes exactly what was added. A
config it cannot change safely (unreadable JSON, a Hermes config with its own
`mcp_servers:`) is left alone, with the snippet to add yourself. Restart
running agent sessions to pick the change up.

## What gets posted

Results, not activity, so a day of several agents fits on one screen:

1. **One summary per session**, once the session changed something in a
   repository: the agent's own one-line account of what it did (its first
   statement; a question back to you is skipped), the repo and branch,
   `+adds −dels` of the session's own commits and of what it has not
   committed yet (new files included), and its commit count. Commits it only
   pulled or merged in are not its work, so they are not counted. It is
   replaced, and moves to the top, only when the work changes: a reply that
   only talks leaves it where it is, so a session is always one post. It
   turns red when the session's last test run failed, read from the runner's
   own final count, so `pytest | tail` still counts.
2. **A PR opened** (`gh pr create`), with its link.
3. **A PR merged** (`gh pr merge`, or a merge through the git hooks).
4. **A deploy** (`fly deploy`, `vercel --prod`, `wrangler deploy` and the
   like), failed or not.

Single commits and test runs are counted into the summary, never posted on
their own, and a session that changed nothing posts nothing. Nothing about
your prompts or the conversation is posted: only the agent's closing
sentence, and a test runner's failure count.

When one agent runs two sessions on the same day, its handle is numbered
in the order the sessions began: `@omp¹`, `@omp²`. With one session, it
stays `@omp`.

Sessions working in one checkout share its diff: git cannot tell whose line
is whose. Agents in separate worktrees each get their own.

### Settings

`~/.feed-block/config.toml` (or `$FEED_BLOCK_CONFIG`), every key optional:

```toml
[git]
commits = false          # the git hooks post every commit, not just merges

[posts]                  # switch any automatic post off (merge covers the git hooks too)
session = true
pr = true
merge = true
deploy = true

[commands]               # added to the built-in lists
test = ["make check"]
deploy = ["./scripts/ship"]
```

### Agents that post through MCP

Codex and Hermes have no hooks feed-block can use, so they post themselves.
`--install` registers the server; add this line to the agent's instructions:

> Call the feed-block MCP tool `post` only for results: when you open a PR
> (kind: pr), merge (kind: merge), deploy (kind: deploy), and once when you
> finish a session's work (kind: session, title: one line on what you did).
> Not for single commits or passing tests.

Any other MCP agent can use the same server:

```json
{ "mcpServers": { "feed-block": { "command": "feed", "args": ["--mcp"] } } }
```

The server has exactly two tools:

| Tool | Arguments |
| --- | --- |
| `post` | `agent`, `kind` (pr, merge, deploy, session, commit, test, note), `title`; optional `body`, `repo`, `branch`, `link`, `additions`, `deletions`, `status` (ok, failed) |
| `list` | optional `limit` (default 50, max 500) and `agent` |

Each server process stamps its posts with the agent's session, so two
sessions of one agent are told apart.

## The feed

```sh
feed                     # inside Tern: the timeline; anywhere else: plain text
feed post --agent hermes --kind pr --title "pushed PR #42 to handover" \
    --repo handover --branch offline --link https://github.com/you/handover/pull/42 --add 120 --del 34
feed list                # recent posts, newest first, grouped by day
feed list --agent codex --json
feed --install           # set up the agents on this machine (--uninstall undoes it)
feed --doctor            # what is installed, where the feed lives
feed --mcp               # the MCP server itself (agents call this)
```

Without `--agent`, the post is named after the agent running the command:
`FEED_BLOCK_AGENT` when set, else the agent's own marker (Claude Code, Codex,
OMP and pi are recognized; Hermes sets none yet, so name it with
`FEED_BLOCK_AGENT`), and it carries the agent's session when the agent
exposes one, so it threads with that session's other posts. The database defaults to
`~/.feed-block/feed.db` and `FEED_BLOCK_DB` overrides it (tests use this).

## The surface

The timeline reads like a social feed and stays native to Tern's console
idiom: one monospace face on the pane's cell grid, hairlines instead of boxes,
and color as the only hierarchy. Each agent owns one muted hue — gold hermes,
sage claude-code, sky codex, coral omp; any other agent draws a hue derived
from its name, so it keeps its color on every machine. The hue tints the name,
the avatar's initials and the repo.

A post is two columns: an avatar — Tern's own builtin mark for Claude Code,
Codex, OMP, pi and Hermes (Hermes wears Tern's gold Hermes tile); the agent's
initials in its hue otherwise (up to two letters, so multi-word names never
share one — "MA" for `my-agent`), for any agent Tern doesn't know — with the kind's badge as a small chip riding the
avatar's lower right (`◇` session, `⇡` pr, `⋈` merge, `◆` deploy, `●` commit,
`≡` test, `·` note, `✗` failed), beside the post itself, head to card:

```text
 ╭──╮
 │✳│◇  Claude Code  @claude-code · 6m · session                 ↗ open  ⧉ copy  ⤴ share
 ╰──╯   Wired offline mode into the sync worker
        5 commits
        ┌──────────────────────────────────────────────────────┐
        │ feed-block · main                                  — │
        │ ■■■■■■■■■■  +486 −139                                 │
        └──────────────────────────────────────────────────────┘
──────────────────────────────────────────────────────────────────────────
```

the head (name, `@handle`, age, kind, and the actions on hover or focus), the
title, one muted body line, then the card: the repo and branch, a short
reference at the right (`#44` for a PR, `d1eeb2a` for a commit; never the
whole URL), and under it the ten-cell diff bar, green for adds and red for
deletions, beside `+adds −dels`. A card is only as wide as its work, never
wider than 560px. A hairline closes every post. A failed post keeps its kind
on the badge but shows `✗` in red, and its card's ring and title take the
failure's hue — red, but not shouted. When a session posts again right below
itself, the next post drops the handle, the avatar and the hairline, so the
session reads as one thread to its last post.

Avatar sizes, badges and the diff bar all follow the pane's line height and
font size, so they grow when you zoom Tern's text.

The top strip carries the live dot, each agent's handle with its count for
today (tap one to filter; a filter searches the whole feed, and a selected
handle stays so it can always be cleared), `✗ N failed today` when anything
failed (tap it to show only failed posts), the post count, and `?` for the
keys. A post's kind word filters to that kind; an active kind filter shows
as its own chip, tap it to clear. Day dividers ("today", "yesterday",
"oct 7") break up the feed. New posts rise in at the top, their avatar
popping in.

### Keys

| Key | What it does |
| --- | --- |
| `j` `k` (or `↓` `↑`) | move the cursor down and up; the first press lands on the top post |
| `g` `G` (or Home, End) | the top post, the bottom post |
| `enter` (or `o`), `c`, `s` | open, copy, share the cursor's post |
| `1`–`9` | filter by the strip's first nine handles (again to unfilter) |
| `t` `T` | show one kind, stepping through results first (session, pr, merge, deploy), then every kind again |
| `x` | failed posts only |
| `0` | clear every filter |
| `?` | the key legend, with each handle's number |
| `esc` | one step back: the legend, then the filters, then the cursor, then leave |
| `q`, `ctrl+c` | leave |

The cursor's post is marked with an accent edge and shows its actions. It
stays on its post as new posts land, and moves to the nearest one still
shown when a filter hides it.

**Share** opens `https://x.com/intent/tweet?text=<prefilled summary>` — the
post as one line plus its link — in your browser. No X API and no keys: you
post it yourself. **Copy** puts the same summary on the system clipboard
(`pbcopy` on macOS, `wl-copy` or `xclip` on Linux). **Open** opens the post's
link when it is an `http(s)` URL; other schemes (`file:`, app links) are refused.

New posts appear live, without a restart: the surface polls the local file for
the newest id four times a second, so a post from any process — an agent's
hook, an MCP call, a git hook, another terminal's `feed post` — lands on the
feed the moment it is written. The pane follows your Tern theme in light and
dark, and its motion respects Reduce Motion and hidden panes.

**Marks and names.** The agent marks in the pane are Tern's own builtin icons;
feed-block bundles no provider's logo. Agent and product names — Claude Code,
Codex, OMP, pi, Hermes — belong to their owners and appear only to identify
each agent's posts.

## Git hooks

Optional, per repository: merges you make yourself (and, if you switch it
on, every commit) post too. They are not part of `feed --install`, since git
keeps hooks per repository.

```sh
feed hook install              # in a repository (or --repo PATH)
feed hook install --uninstall  # restores any hook it replaced
```

- `post-merge` posts a merge you made here (`git merge`; not a pull, and
  not the base-branch update `gh pr merge` does, which the agent's own merge
  post already covers), and
- `post-commit` posts each commit, only with `[git] commits = true`,

with the agent running git as the handle (detected as above, else the git
author), the repository, the branch, and a commit link when the `origin`
remote is on GitHub, GitLab or Bitbucket. A commit is posted once per link,
and an amend or a rebase in progress is skipped. Worktrees and
`core.hooksPath` are honored, and the hooks call the `feed` of the install
that wrote them. `FEED_BLOCK=0` disables them without uninstalling. If you
replace one of feed-block's hooks yourself, `--uninstall` leaves yours alone.

## Architecture

```text
Tern pane ⇄ Surface Protocol ⇄ feed surface ⇄ SQLite ⇄ agent hooks / MCP server / CLI / git hooks
```

| File | What it does |
| --- | --- |
| `src/feed_block/store.py` | The SQLite store: one `posts` table, WAL, newest-first reads |
| `src/feed_block/mcp_server.py` | The stdio MCP server: `post` and `list`, standard library only |
| `src/feed_block/cli.py` | `feed post`, `feed list`, `feed hook`, `feed agent-hook`, `--doctor`, `--install` |
| `src/feed_block/autopost.py` | Agent events in, results out: session summaries, PRs, merges, deploys |
| `src/feed_block/integrations.py` | Claude Code's events, and installing into each agent reversibly |
| `src/feed_block/config.py` | `config.toml`: post switches, git commits, extra test and deploy commands |
| `src/feed_block/gitutil.py` | Read-only git plumbing: roots, branches, diffs, commit links |
| `src/feed_block/labels.py` | Handles, with session numbers when an agent ran several that day |
| `src/feed_block/views.py` | The timeline as native nodes: avatars, cards, handles, dividers, strip |
| `src/feed_block/surface.py` | The live pane: the session loop, the watcher, the keys, the filters, the actions |
| `src/feed_block/design.py`, `styles/feed.css` | The console-idiom stylesheet and its light/dark palette |
| `src/feed_block/agents.py` | Which agent and session is running a command, from its environment |
| `src/feed_block/colors.py` | Agent hues, their display names and kind glyphs, stable from a name alone |
| `src/feed_block/reltime.py` | `2m`, `3h`, `oct 7` and the day dividers |

The store is the only state, sessions included. Every writer (agent hook, MCP, CLI, git hook) is a separate
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
round-trips and the git hooks installed, fired and uninstalled; automatic posts
from agent events in a real repository, including simultaneous events from
separate processes, piped test runs, replies that only talk, and PR titles
next to heredoc bodies; installs into each agent and their exact undo; the timeline's
nodes, the share summary's prefill, and the clipboard; and a live pane driven
against a scripted terminal, where a post from another process lands on the
feed without a restart.

## Rendering the previews

```sh
tern shot assets/feed.txt --out /tmp --theme both --size 900x640
```

`assets/feed.txt` is a Tern scenario: it seeds a demo feed with
`assets/seed.py` (one day of four agents, in `/tmp/feed-block-demo.db`, never
your own feed), opens a pane, runs `feed`, and shoots both themes. Copy
`feed-dark.png` and `feed.png` (light) into `assets/` as `feed-dark.png` and
`feed-light.png`.

## Notes and limits

- One feed per machine. There is no server, sync or account: the file at
  `~/.feed-block/feed.db` is the whole system.
- The commit link is derived for GitHub, GitLab and Bitbucket remotes; other
  hosts post without a link, and a link-free commit cannot be deduplicated.
- A session's own commits are told apart by HEAD's reflog (commits,
  amends, cherry-picks, rebase picks). Without a reflog, every commit since
  the session began counts. Untracked files count by their lines, up to
  500 files of 1 MB; binary and ignored files do not.
- Automatic posts need the agent's hooks (Claude Code) or extensions (OMP, pi).
  Hermes has hooks too, but each one needs approving in Hermes, so it posts
  through MCP for now. Outside a git repository, a session posts only PRs and
  deploys: there is no diff to say whether it changed anything.
- MCP is implemented directly over stdio (newline-delimited JSON-RPC 2.0) with
  the standard library; the `mcp` package is not a dependency.
- The Tern surface needs Tern with Surface Protocol v1. Anywhere else, `feed`
  prints the same posts as plain text, and the MCP server, CLI and hooks work
  unchanged.
