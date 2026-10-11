"""Agent colors, Tern's builtin marks, and kind glyphs: stable, muted, and the
same in light and dark.

Named agents own their hue (gold hermes, sage claude-code, sky codex, coral omp);
any other agent gets a hue derived from its name, so the same agent keeps its
color on every machine and every run without any state. Avatars show Tern's
builtin mark when it carries one; the rest show tinted initials.
"""

from __future__ import annotations

import hashlib
import re

# (light, dark) pairs, in the same muted register as Tern's console palettes.
NAMED: dict[str, tuple[str, str]] = {
    "hermes": ("#805c20", "#d5b475"),
    "claude-code": ("#4f7a2e", "#a9c98c"),
    "codex": ("#2f6a9a", "#8fb8de"),
    "omp": ("#a8502c", "#e39b7b"),
}

# Hues an unknown agent draws from, by a hash of its name.
HUES: list[tuple[str, str]] = [
    ("#2f7f78", "#7fc4bf"),  # teal
    ("#a14a63", "#e3a8b8"),  # rose
    ("#55647a", "#aab8c8"),  # slate
    ("#5f7a2e", "#b8c98c"),  # lime
    ("#9a6b12", "#e0c48a"),  # amber
    ("#2f6f7a", "#8fd0d8"),  # cyan
    ("#8a4a7a", "#d29ad0"),  # magenta
    ("#7a5a2f", "#d8bd8f"),  # sand
]

# One glyph per kind, worn on a post's kind badge; a failed post wears ✗ whatever its kind.
# Merge is a join, not a check mark: ✓ would read as "passed".
GLYPHS = {"commit": "●", "pr": "⇡", "merge": "⋈", "test": "≡", "deploy": "◆", "note": "·", "session": "◇"}
FAILED_GLYPH = "✗"

# Tern's own builtin marks, used as avatars where it carries one; the pane is
# then not bundled with any provider's logo. Anything else keeps its initials.
MARKS: dict[str, str] = {
    "claude-code": "app-claude",
    "codex": "app-codex",
    "omp": "app-omp",
    "pi": "pi-mark",
    "hermes": "tile-hermes",
}


# Who an agent is, when its name is not its name. Anything else is presented
# title-cased, so `my-agent` becomes "My Agent".
DISPLAY_NAMES: dict[str, str] = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "omp": "OMP",
    "pi": "pi",
    "hermes": "Hermes",
}


def slug(agent: str) -> str:
    """A CSS-safe fragment of an agent name."""
    return re.sub(r"[^a-z0-9]+", "-", agent.lower()).strip("-") or "agent"


def display_name(agent: str) -> str:
    """An agent as the feed presents it: the known name, else the name title-cased."""
    name = DISPLAY_NAMES.get(agent.lower())
    if name is not None:
        return name
    return re.sub(r"[-_]+", " ", agent.strip()).title() or agent.strip()


def initials(agent: str) -> str:
    """A fallback avatar's initials: the first letters of up to two words, upper case."""
    words = re.sub(r"[-_]+", " ", display_name(agent).strip()).split()
    return "".join(w[:1].upper() for w in words[:2]) or "?"


def mark(agent: str) -> str | None:
    """Tern's builtin mark for an agent, or None when it falls back to initials."""
    return MARKS.get(agent.lower())


def hue(agent: str) -> tuple[str, str]:
    """The (light, dark) color pair for any agent, named or not."""
    return NAMED.get(agent.lower()) or HUES[_bucket(agent)]


def _bucket(agent: str) -> int:
    digest = hashlib.sha256(agent.lower().encode()).digest()
    return int.from_bytes(digest[:4], "big") % len(HUES)


def agent_class(agent: str) -> str:
    """The class carrying the agent's color, unique even across colliding names."""
    if agent.lower() in NAMED:
        return f"fb-agent-{slug(agent)}"
    digest = hashlib.sha256(agent.lower().encode()).hexdigest()[:4]
    return f"fb-agent-{slug(agent)}-{digest}"


def _rule(cls: str, pair: tuple[str, str]) -> str:
    light, dark = pair
    return f".{cls} {{ --fb-agent: light-dark({light}, {dark}); }}"


def rules(agents: list[str] | None = None) -> str:
    """The stylesheet's color rules: the named agents, then one per other agent."""
    lines = [_rule(f"fb-agent-{name}", pair) for name, pair in NAMED.items()]
    for agent in agents or []:
        if agent.lower() in NAMED:
            continue
        lines.append(_rule(agent_class(agent), hue(agent)))
    return "\n".join(lines) + "\n"


def glyph(kind: str, failed: bool = False) -> str:
    return FAILED_GLYPH if failed else GLYPHS.get(kind, "·")
