"""The "pitwall" theme: red on black, brutalist.

Status colours are a deliberately separate scale from theme chrome — they must
never be dimmed or tinted by the surrounding panel.
"""

from __future__ import annotations

from textual.theme import Theme

from .model import Severity

BACKGROUND = "#0a0a0a"
SURFACE = "#141414"
PANEL = "#1c1c1c"
BORDER = "#2a2a2a"
BORDER_FOCUS = "#e10600"
PRIMARY = "#e10600"
ACCENT = "#ff1e00"
TEXT = "#e8e8e8"
TEXT_MUTED = "#6b6b6b"
TEXT_DIM = "#3d3d3d"

SEVERITY_COLOURS: dict[Severity, str] = {
    Severity.OK: "#7a7a7a",  # quiet on purpose: most cells are fine and should recede
    Severity.ACTIVE: "#e8e8e8",
    Severity.WARN: "#ffb000",
    Severity.ERROR: "#ff1e00",
}

PITWALL = Theme(
    name="pitwall",
    primary=PRIMARY,
    secondary=ACCENT,
    accent=ACCENT,
    warning=SEVERITY_COLOURS[Severity.WARN],
    error=SEVERITY_COLOURS[Severity.ERROR],
    success=SEVERITY_COLOURS[Severity.ACTIVE],
    foreground=TEXT,
    background=BACKGROUND,
    surface=SURFACE,
    panel=PANEL,
    dark=True,
    variables={
        "border": BORDER,
        "border-focus": BORDER_FOCUS,
        "text-muted": TEXT_MUTED,
        "text-dim": TEXT_DIM,
        "status-ok": SEVERITY_COLOURS[Severity.OK],
        "status-active": SEVERITY_COLOURS[Severity.ACTIVE],
        "status-warn": SEVERITY_COLOURS[Severity.WARN],
        "status-error": SEVERITY_COLOURS[Severity.ERROR],
        "block-cursor-background": PRIMARY,
        "block-cursor-foreground": BACKGROUND,
        "block-cursor-text-style": "none",
        "scrollbar": PANEL,
        "scrollbar-hover": BORDER,
        "scrollbar-active": PRIMARY,
        "footer-key-foreground": PRIMARY,
        "footer-description-foreground": TEXT_MUTED,
    },
)
