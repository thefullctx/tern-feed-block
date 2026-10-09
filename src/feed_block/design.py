"""Packaged visual assets: the stylesheet, its color rules, and the palette."""

from __future__ import annotations

from importlib.resources import files

from . import colors

ASSETS = files("feed_block").joinpath("styles")
BASE_CSS = ASSETS.joinpath("feed.css").read_text()

# The surface's own accent and its neutral scale, light then dark, so the pane
# follows the user's Tern theme.
DARK = {
    "accent": "#c3a6e0",
    "text": "#ece8df",
    "muted": "#aaa397",
}
LIGHT = {
    "accent": "#6b4f9a",
    "text": "#292722",
    "muted": "#686359",
}


def stylesheet(agents: list[str] | None = None) -> str:
    """The pane's stylesheet: the console idiom plus one color rule per agent."""
    return BASE_CSS + "\n" + colors.rules(agents)
