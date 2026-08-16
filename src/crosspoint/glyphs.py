"""Every glyph in the app. None inline in widgets.

Two parallel sets: Nerd Font (default) and ASCII (`--ascii`). `use_ascii()` swaps
the module-level names, so widgets read `glyphs.SUB_ERROR` and never branch.
All matrix glyphs are single-cell width — the grid must stay aligned.
"""

from __future__ import annotations

NERD = {
    "DEVICE_ONLINE": "",  #  filled circle
    "DEVICE_OFFLINE": "",  #  hollow circle
    "SUB_OK": "●",  # ●
    "SUB_ACTIVE": "◆",  # ◆
    "SUB_WARN": "▲",  # ▲
    "SUB_ERROR": "✕",  # ✕  — distinct shape, never colour alone
    "SUB_NONE": "·",
    "GROUP_CLOSED": "▸",  # collapsed device block
    "GROUP_OPEN": "▾",  # expanded device block  # ·
    "CLOCK_MASTER": "",  #
    "CLOCK_SLAVE": "",  #
    "CLOCK_UNLOCKED": "",  #
    "SAMPLE_RATE": "",  #
    "LATENCY": "",  #
    "FILTER": "",  #
    "SEARCH": "",  #
    "LOCK": "",  # nf-fa-lock
}

ASCII = {
    "DEVICE_ONLINE": "*",
    "DEVICE_OFFLINE": "o",
    "SUB_OK": "o",
    "SUB_ACTIVE": "#",
    "SUB_WARN": "!",
    "SUB_ERROR": "X",
    "SUB_NONE": ".",
    "GROUP_CLOSED": ">",
    "GROUP_OPEN": "v",
    "CLOCK_MASTER": "M",
    "CLOCK_SLAVE": "s",
    "CLOCK_UNLOCKED": "!",
    "SAMPLE_RATE": "~",
    "LATENCY": "t",
    "FILTER": "=",
    "SEARCH": "/",
    "LOCK": "+",
}

globals().update(NERD)

# Declared for type checkers and for readers grepping the module.
DEVICE_ONLINE: str
DEVICE_OFFLINE: str
SUB_OK: str
SUB_ACTIVE: str
SUB_WARN: str
SUB_ERROR: str
SUB_NONE: str
GROUP_CLOSED: str
GROUP_OPEN: str
CLOCK_MASTER: str
CLOCK_SLAVE: str
CLOCK_UNLOCKED: str
SAMPLE_RATE: str
LATENCY: str
FILTER: str
SEARCH: str
LOCK: str


def use_ascii(enabled: bool = True) -> None:
    """Swap the whole glyph set. Call once at startup, before widgets are built."""
    globals().update(ASCII if enabled else NERD)


def severity_glyph(severity: str) -> str:
    """`Severity.value` -> glyph. Read at call time so `use_ascii` is respected."""
    return globals()[f"SUB_{severity.upper()}"]
