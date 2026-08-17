"""The TUI. Never imports a backend — one is injected at startup."""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from functools import partial
from typing import Any, ClassVar, cast, override

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Input, Label, ListItem, ListView, RichLog, Static, TabbedContent, TabPane

from . import glyphs
from .backends import Backend
from .matrix import Matrix
from .model import Device, Severity, Snapshot
from .theme import PITWALL, SEVERITY_COLOURS, TEXT_DIM

TABS = ["devices", "routing", "clock", "events"]
FILTER_DEBOUNCE = 0.15  # seconds of quiet before the matrix rebuilds

# Estates name devices by location — "CR03-Ceiling-Mic", "court2_dsp". The leading
# token is the room, and it is the only grouping key that needs no configuration.
_PREFIX = re.compile(r"[-_. ]")

# Plain words for the four severities. "ACTIVE" and "OK" mean nothing to someone
# who has not read the Dante status table.
LEGEND: tuple[tuple[Severity, str], ...] = (
    (Severity.ACTIVE, "connected"),
    (Severity.OK, "idle"),
    (Severity.WARN, "warning"),
    (Severity.ERROR, "error"),
)


def _group_of(name: str) -> str:
    return _PREFIX.split(name, maxsplit=1)[0]


def _rate(hz: int | None) -> str:
    """48000 -> '48 kHz'. Nobody reads six digits at a glance."""
    return "-" if hz is None else f"{hz / 1000:g} kHz"


def _latency(us: int | None) -> str:
    """Microseconds on the wire, milliseconds on screen — a latency setting is
    written in ms on every device front panel, so it should read that way here."""
    if us is None:
        return "-"
    return f"{us / 1000:g} ms" if us >= 1000 else f"{us} us"


def _legend_text() -> str:
    """Read the glyphs at call time so `--ascii` is respected."""
    parts = [
        f"[{SEVERITY_COLOURS[severity]}]{glyphs.severity_glyph(severity.value)}[/] {word}"
        for severity, word in LEGEND
    ]
    parts.append(f"[$text-dim]{glyphs.SUB_NONE}[/] no route")
    return "  ".join(parts) + "   [$text-dim]click any cell or name[/]"


def _clock_glyph(device: Device) -> tuple[str, str]:
    """(glyph, colour) for a device's clock state."""
    clock = device.clock
    if clock is None or clock.role == "unknown":
        return glyphs.CLOCK_UNLOCKED, SEVERITY_COLOURS[Severity.WARN]
    if clock.sync_state != "locked":
        return glyphs.CLOCK_UNLOCKED, SEVERITY_COLOURS[Severity.ERROR]
    if clock.role == "master":
        return glyphs.CLOCK_MASTER, SEVERITY_COLOURS[Severity.ACTIVE]
    return glyphs.CLOCK_SLAVE, SEVERITY_COLOURS[Severity.OK]


class HelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape,question_mark,q", "dismiss", "Close")]

    @override
    def compose(self) -> ComposeResult:
        yield Static(_help_text(), id="help-body")


class DeviceProvider(Provider):
    """Command-palette entries for moving to a device in the matrix."""

    @override
    async def search(self, query: str) -> AsyncIterator[Hit]:
        matcher = self.matcher(query)
        app = cast(CrosspointApp, self.app)
        for device in app.snapshot.devices:
            score = matcher.match(device.name)
            if score > 0:
                yield Hit(
                    score,
                    device.name,
                    lambda name=device.name: app.action_jump_to_device(name),
                    help="Jump to this device",
                )

    @override
    async def discover(self) -> AsyncIterator[Hit]:
        app = cast(CrosspointApp, self.app)
        for device in app.snapshot.devices:
            yield Hit(
                0,
                device.name,
                lambda name=device.name: app.action_jump_to_device(name),
                help="Jump to this device",
            )


def _help_text() -> str:
    """Render the key reference from the bindings users can actually invoke."""
    sections: list[tuple[str, list[Binding]]] = [
        ("app", list(CrosspointApp.BINDINGS)),
        ("routing", list(Matrix.BINDINGS)),
    ]
    lines = [
        "[b]crosspoint[/b] — unofficial read-only TUI for Dante and AES67",
        "",
        "[b $accent]if you have never used this before[/]",
        "  The mouse works everywhere. Click a device to see it, click a cell in",
        "  the routing grid to see what is wired to what, click a device name to",
        "  open its channels. Arrow keys and Enter work too.",
        "  Rows are receivers (they listen). Columns are transmitters (they send).",
        f"  [{SEVERITY_COLOURS[Severity.ERROR]}]{glyphs.SUB_ERROR}[/] and "
        f"[{SEVERITY_COLOURS[Severity.WARN]}]{glyphs.SUB_WARN}[/] are the ones to chase; "
        f"press [b]n[/b] to jump to the next.",
        "",
    ]
    for title, bindings in sections:
        lines.append(f"[b $accent]{title}[/]")
        for binding in bindings:
            if binding.description:
                keys = binding.key.replace(",", " / ")
                lines.append(f"  {keys:<24} {binding.description}")
        lines.append("")
    lines.append("This build is READ ONLY. Nothing here writes to a device.")
    return "\n".join(lines)


class Sidebar(ListView):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "first", "Top", show=False),
        Binding("G", "last", "Bottom", show=False),
    ]

    def action_first(self) -> None:
        self.index = self._nearest(0, 1)

    def action_last(self) -> None:
        self.index = self._nearest(max(0, len(self) - 1), -1)

    def _nearest(self, start: int, step: int) -> int:
        """First selectable item from `start` — group headers are disabled rows."""
        for index in range(start, len(self) if step > 0 else -1, step):
            if not self._nodes[index].disabled:
                return index
        return start


class CrosspointApp(App[None]):
    CSS: ClassVar[str] = """
    Screen { background: $background; }

    /* 1 row means 1 row: a border here would eat the only content line. */
    #header { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    #body { height: 1fr; }

    #sidebar {
        width: 28; background: $surface; border-right: solid $border;
        padding: 0; height: 1fr;
    }
    #sidebar.hidden { display: none; }
    Sidebar > ListItem { background: $surface; padding: 0 0 0 1; }
    Sidebar > ListItem.--highlight { background: $surface; border-left: solid $primary; padding: 0; }
    Sidebar:focus > ListItem.--highlight { background: $surface; }
    /* Group headers are disabled ListItems; Textual would otherwise fade them
       to the point of being unreadable, and they are the navigation aid. */
    Sidebar > ListItem:disabled { background: $panel; text-opacity: 100%; }

    TabbedContent { width: 1fr; height: 1fr; }
    TabbedContent > ContentSwitcher { height: 1fr; }
    Tabs { background: $surface; }
    Tabs > #tabs-list { background: $surface; }
    TabPane { padding: 0 1; background: $background; }

    /* The estate table gets the room; the detail block sits under it. */
    #device-table { height: 1fr; }
    #devices-body {
        height: auto; max-height: 12; padding: 0 1;
        border-top: solid $border; background: $surface;
    }

    /* Border is always present so focus does not reflow the grid. */
    Matrix { height: 1fr; background: $background; border-left: solid $border; }
    Matrix:focus { border-left: solid $border-focus; }

    /* Always-on: at 200 devices you must never have to guess where you are. */
    #matrix-status { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    /* The grid is glyphs. Without a key on screen it is a wall of shapes. */
    #matrix-legend { height: 1; background: $surface; padding: 0 1; }

    #detail {
        height: auto; max-height: 10; background: $surface;
        border-top: solid $border; padding: 0 1; display: none;
    }
    #detail.open { display: block; }

    #filter { display: none; border: none; background: $surface; height: 1; padding: 0 1; }
    #filter.open { display: block; }

    DataTable { background: $background; }
    DataTable > .datatable--header { background: $panel; color: $accent; text-style: bold; }
    DataTable > .datatable--cursor { background: $primary; color: $background; }

    #events-body { padding: 1 1; color: $text-muted; }

    HelpScreen { align: center middle; }
    #help-body {
        width: 62; height: auto; background: $surface;
        border: solid $border-focus; padding: 1 2;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "help", "Help"),
        Binding("ctrl+p", "command_palette", "Find a device"),
        Binding("backslash", "toggle_sidebar", "Device list"),
        Binding("slash", "filter", "Filter the grid"),
        Binding("escape", "clear", "Clear the filter"),
        Binding("r", "refresh", "Reload"),
        Binding("s", "sort", "Sort by next column"),
        Binding("S", "reverse_sort", "Reverse the sort", show=False),
        *[Binding(str(i + 1), f"tab('{name}')", name.title()) for i, name in enumerate(TABS)],
    ]
    COMMANDS: ClassVar[set[type[Provider]]] = {DeviceProvider}

    def __init__(self, backend: Backend, iface: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Before the screen is registered: CSS is parsed then, and it references
        # the theme's variables.
        self.register_theme(PITWALL)
        self.theme = "pitwall"
        self.backend = backend
        self.iface = iface
        self.snapshot = Snapshot()
        self._refreshed_at = time.monotonic()
        self._filter_timer: Timer | None = None
        self._event_source: tuple[str, ...] = ()
        # (column key, descending) per sortable table.
        self._sort: dict[str, tuple[str, bool]] = {
            "clock-table": ("role", False),
            "device-table": ("device", False),
        }
        self._selected: str | None = None  # one selected device, shared by both lists
        self._broken: dict[str, int] = {}  # problem tally per device, once per refresh

    # ---- layout ---------------------------------------------------------

    @override
    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="body"):
            # No scroll wrapper: ListView already scrolls, and nesting the two
            # gave the sidebar two scrollbars fighting over one list.
            yield Sidebar(id="sidebar")
            with TabbedContent(id="tabs"):
                with TabPane("1 Devices", id="devices"), Vertical():
                    yield DataTable(id="device-table", cursor_type="row", zebra_stripes=False)
                    yield VerticalScroll(Static(id="devices-body"))
                with TabPane("2 Routing", id="routing"), Vertical():
                    yield Matrix(id="matrix")
                    yield Static(id="matrix-legend")
                    yield Static(id="matrix-status")
                    yield Input(placeholder="filter rows; tx: filters columns", id="filter")
                    yield Static(id="detail")
                with TabPane("3 Clock", id="clock"):
                    yield DataTable(id="clock-table", cursor_type="row", zebra_stripes=False)
                with TabPane("4 Events", id="events"):
                    yield VerticalScroll(RichLog(id="events-body", markup=False, wrap=True))
        yield Footer()

    async def on_mount(self) -> None:
        for table_id in self._sort:
            table = self.query_one(f"#{table_id}", DataTable)
            for key, label, width in self._columns(table_id):
                table.add_column(label, key=key, width=width)
        self._paint("#matrix-legend", _legend_text())
        await self.action_refresh()

    # ---- data -----------------------------------------------------------

    async def action_refresh(self) -> None:
        self.snapshot = await self.backend.snapshot()
        self._refreshed_at = time.monotonic()
        self._broken = self._problem_counts()  # once: the sidebar and both tables want it
        # Decide the selection before anything is filled. Both lists then open on
        # the same device, instead of it falling out of whichever list's
        # Highlighted message happens to be dispatched last.
        if self._selected is None and self.snapshot.devices:
            self._selected = self.snapshot.devices[0].name
        matrix = self.query_one("#matrix", Matrix)
        matrix.load(self.snapshot)
        self._paint_matrix_status(matrix)
        self._fill_sidebar()
        self._fill_devices()
        self._fill_clock()
        self._append_events(self.snapshot.events)
        self._paint_header()
        self._paint_device()

    def _append_events(self, events: tuple[str, ...]) -> None:
        """Append only the part of a snapshot event sequence not seen before."""
        previous = self._event_source
        if events[: len(previous)] == previous:
            new = events[len(previous) :]
        else:
            overlap = max(
                (
                    size
                    for size in range(1, min(len(previous), len(events)) + 1)
                    if previous[-size:] == events[:size]
                ),
                default=0,
            )
            new = events[overlap:]
        self._event_source = events
        log = self.query_one("#events-body", RichLog)
        for event in new:
            log.write(event)

    def _fill_sidebar(self) -> None:
        """Repopulate the device list, keeping the selected *device* rather than
        the selected index — devices come and go between refreshes.

        At estate scale the list is grouped by name prefix, so 200 devices read
        as the dozen rooms they actually live in.

        Filling a list is not a user picking something out of it, so the
        Highlighted messages it emits are suppressed — the caller has already
        decided what is selected. Letting them out is what made the sidebar and
        the Devices table echo each other for thousands of rounds per refresh.
        """
        sidebar = self.query_one(Sidebar)
        devices = self.snapshot.devices
        selected = self._selected
        broken = self._broken

        groups: dict[str, list[Device]] = {}
        for device in devices:
            groups.setdefault(_group_of(device.name), []).append(device)
        # Group only where grouping actually groups. Six devices with six
        # different prefixes would just gain six useless header rows.
        # ponytail: self-tuning ratio instead of a configurable threshold —
        # give it a setting if a real estate ever groups badly.
        headers = 1 < len(groups) <= len(devices) / 2

        index = 0
        restored = None
        with sidebar.prevent(ListView.Highlighted):
            sidebar.clear()
            for group, members in groups.items():
                if headers:
                    hurt = sum(broken.get(d.name, 0) for d in members)
                    offline = sum(1 for d in members if not d.online)
                    sidebar.append(
                        ListItem(Label(self._group_row(group, members, hurt, offline)), disabled=True)
                    )
                    index += 1
                for device in members:
                    sidebar.append(
                        ListItem(
                            Label(self._sidebar_row(device, broken.get(device.name, 0), headers)),
                            name=device.name,
                        )
                    )
                    if device.name == selected:
                        restored = index
                    index += 1
            sidebar.index = restored if restored is not None else sidebar._nearest(0, 1)

    def _group_row(self, group: str, members: list[Device], hurt: int, offline: int) -> str:
        """A group header: where to look, and whether to bother looking.

        Fixed-width slots, glyph-tagged — "8 1 off 1" is three numbers nobody can
        tell apart, "8  ○1  ✕1" is a count, an outage and a fault.
        """
        error = SEVERITY_COLOURS[Severity.ERROR]
        down = f"[{error}]{glyphs.DEVICE_OFFLINE}{offline:<2}[/]" if offline else "   "
        tally = f"[{error}]{glyphs.SUB_ERROR}{hurt:<3}[/]" if hurt else "    "
        return f"[b $accent]{group[:12]:<12}[/] [$text-muted]{len(members):>2}[/] {down}{tally}"

    def _problem_counts(self) -> dict[str, int]:
        """WARN/ERROR subscriptions per RX device, for the sidebar."""
        counts: dict[str, int] = {}
        for sub in self.snapshot.subscriptions:
            if sub.severity in (Severity.WARN, Severity.ERROR):
                counts[sub.rx_device] = counts.get(sub.rx_device, 0) + 1
        return counts

    def _sidebar_row(self, device: Device, broken: int, grouped: bool = False) -> str:
        if device.online:
            mark, colour = glyphs.DEVICE_ONLINE, SEVERITY_COLOURS[Severity.ACTIVE]
        else:
            mark, colour = glyphs.DEVICE_OFFLINE, SEVERITY_COLOURS[Severity.ERROR]
        clock_mark, clock_colour = _clock_glyph(device)
        name_style = f"[{colour}]" if not device.online else ""
        name_end = "[/]" if not device.online else ""
        # Under a group header the prefix is already on screen, and dropping it
        # buys back the characters that actually tell two devices apart.
        label = device.name
        if grouped:
            label = label[len(_group_of(label)) :].lstrip("-_. ") or label
        # A bare count is the cheapest way to see which of 200 devices needs you.
        tally = f"[{SEVERITY_COLOURS[Severity.ERROR]}]{broken:>3}[/]" if broken else "   "
        return (
            f"[{colour}]{mark}[/] {name_style}{label[:16]:<16}{name_end} "
            f"[{clock_colour}]{clock_mark}[/]{tally}"
        )

    # ---- sortable tables ------------------------------------------------
    #
    # Two tables, same behaviour: click a header to sort by it, click again to
    # reverse. `s` and `S` are the keyboard equivalents. Sorting an estate by
    # sample rate or latency is how you find the one device that was set up wrong.

    _clock_columns = (
        ("device", "device", 18),
        ("role", "role", 10),
        ("sync", "sync", 10),
        ("offset", "offset ppm", 12),
        ("external", "ext sync", 9),
        ("source", "source", 18),
    )

    # Widths are chosen to fit an 80-column-ish content area with the sidebar
    # open — DataTable adds 2 cells of padding per column, so the budget is
    # tighter than it looks. The address lives in the detail block below.
    _device_columns = (
        ("device", "device", 18),
        ("state", "state", 9),
        ("problems", "problems", 8),
        ("clock", "clock", 9),
        ("rate", "sample rate", 11),
        ("latency", "latency", 7),
        ("channels", "tx / rx", 7),
    )

    def _columns(self, table_id: str) -> tuple[tuple[str, str, int], ...]:
        return self._clock_columns if table_id == "clock-table" else self._device_columns

    def _ordered_devices(self, table_id: str) -> list[Device]:
        """Devices in this table's sort order. Also paints the header markers."""
        key, reverse = self._sort[table_id]
        table = self.query_one(f"#{table_id}", DataTable)
        for column_key, label, _ in self._columns(table_id):
            if column_key == key:
                label += " v" if reverse else " ^"
            table.columns[cast(Any, column_key)].label = Text(label)
        if table_id == "clock-table":
            value = partial(self._clock_value, column=key)
        else:
            value = partial(self._device_value, column=key, broken=self._broken)
        return sorted(self.snapshot.devices, key=value, reverse=reverse)

    def _refill(self, table_id: str) -> None:
        if table_id == "clock-table":
            self._fill_clock()
        else:
            self._fill_devices()

    def _toggle_sort(self, table_id: str, column: str) -> None:
        """Sort by `column`, or reverse it if it is already the sort column."""
        if column not in {candidate for candidate, _, _ in self._columns(table_id)}:
            return
        current, reverse = self._sort[table_id]
        self._sort[table_id] = (column, not reverse) if column == current else (column, False)
        self._refill(table_id)

    def _visible_table(self) -> DataTable[Any] | None:
        """The sortable table the user is looking at, whether or not it has focus.

        Tab-to-focus is not obvious to a newcomer, so `s` follows the visible tab
        rather than demanding the table be focused first.
        """
        active = self.query_one("#tabs", TabbedContent).active
        table_id = {"clock": "clock-table", "devices": "device-table"}.get(active)
        return None if table_id is None else self.query_one(f"#{table_id}", DataTable)

    def action_sort(self) -> None:
        """Sort by the next column along. A row cursor has no 'current column'
        to sort by, so `s` walks them and `S` reverses."""
        table = self._visible_table()
        if table is None or table.id is None:
            return
        keys = [key for key, _, _ in self._columns(table.id)]
        current, _ = self._sort[table.id]
        step = keys.index(current) + 1 if current in keys else 0
        self._sort[table.id] = (keys[step % len(keys)], False)
        self._refill(table.id)

    def action_reverse_sort(self) -> None:
        table = self._visible_table()
        if table is None or table.id is None:
            return
        self._toggle_sort(table.id, self._sort[table.id][0])

    def _fill_devices(self) -> None:
        """Every device, one row each — the estate on one screen.

        RowHighlighted is suppressed for the same reason as in `_fill_sidebar`:
        clearing the table drags its cursor to row 0, and that is not a device
        anybody chose.
        """
        table = self.query_one("#device-table", DataTable)
        ordered = self._ordered_devices("device-table")
        with table.prevent(DataTable.RowHighlighted):
            table.clear()
            self._add_device_rows(table, ordered)
            # Re-sorting must not silently change which device you were reading.
            row = next((i for i, d in enumerate(ordered) if d.name == self._selected), None)
            if row is not None:
                table.move_cursor(row=row)

    def _add_device_rows(self, table: DataTable[Any], ordered: list[Device]) -> None:
        for device in ordered:
            hurt = self._broken.get(device.name, 0)
            mark, clock_colour = _clock_glyph(device)
            if device.online:
                state = f"[{SEVERITY_COLOURS[Severity.ACTIVE]}]{glyphs.DEVICE_ONLINE} online[/]"
            else:
                state = f"[{SEVERITY_COLOURS[Severity.ERROR]}]{glyphs.DEVICE_OFFLINE} OFFLINE[/]"
            if device.is_locked:
                state += f" {glyphs.LOCK}"
            clock = device.clock
            table.add_row(
                device.name,
                state,
                # DataTable renders cells through rich markup, which does not
                # know Textual's CSS variables — hex only in here.
                f"[{SEVERITY_COLOURS[Severity.ERROR]}]{hurt}[/]" if hurt else f"[{TEXT_DIM}]-[/]",
                f"[{clock_colour}]{mark} {clock.role if clock else 'unknown'}[/]",
                _rate(device.sample_rate),
                _latency(device.latency_us),
                f"{len(device.tx_channels)} / {len(device.rx_channels)}",
                key=device.name,
            )

    @staticmethod
    def _device_value(device: Device, column: str, broken: dict[str, int]):
        name = device.name.casefold()
        if column == "state":
            return (device.online, name)
        if column == "problems":
            # Descending: "sort by problems" means the broken ones first.
            return (-broken.get(device.name, 0), name)
        if column == "clock":
            clock = device.clock
            return (0 if clock and clock.role == "master" else 1, clock.role if clock else "zz", name)
        if column == "rate":
            return (device.sample_rate or 0, name)
        if column == "latency":
            return (device.latency_us or 0, name)
        if column == "channels":
            return (len(device.tx_channels) + len(device.rx_channels), name)
        return name

    def _fill_clock(self) -> None:
        table = self.query_one("#clock-table", DataTable)
        table.clear()
        for device in self._ordered_devices("clock-table"):
            clock = device.clock
            if clock is None:
                table.add_row(device.name, "-", "-", "-", "-", "-")
                continue
            ppm = "-" if clock.frequency_offset_ppm is None else f"{clock.frequency_offset_ppm:+.1f}"
            mark, colour = _clock_glyph(device)
            table.add_row(
                device.name,
                f"[{colour}]{mark} {clock.role}[/]",
                clock.sync_state,
                ppm,
                "yes" if clock.external_sync else "no",
                clock.source or "-",
            )

    @staticmethod
    def _clock_value(device: Device, column: str):
        clock = device.clock
        if column == "device":
            return device.name.casefold()
        if column == "role":
            return (
                0 if clock and clock.role == "master" else 1,
                clock.role if clock else "unknown",
                device.name.casefold(),
            )
        if column == "sync":
            return (clock.sync_state if clock else "unknown", device.name.casefold())
        if column == "offset":
            return (
                clock.frequency_offset_ppm
                if clock and clock.frequency_offset_ppm is not None
                else float("inf"),
                device.name.casefold(),
            )
        if column == "external":
            return (clock.external_sync if clock else False, device.name.casefold())
        return ((clock.source or "") if clock else "", device.name.casefold())

    def _paint_header(self) -> None:
        age = int(time.monotonic() - self._refreshed_at)
        master = self.snapshot.clock_master or "none"
        offline = sum(1 for d in self.snapshot.devices if not d.online)
        count = f"{len(self.snapshot.devices)} devices"
        # The one-glance verdict. Whoever is standing at the rack wants this
        # answered before they read anything else on the screen.
        hurt = sum(self._broken.values())
        if hurt or offline:
            trouble = ", ".join(
                part for part in (
                    f"{hurt} bad route{'s' if hurt != 1 else ''}" if hurt else "",
                    f"{offline} offline" if offline else "",
                ) if part
            )
            verdict = f"[{SEVERITY_COLOURS[Severity.ERROR]}]{glyphs.SUB_ERROR} {trouble}[/]"
        else:
            verdict = f"[{SEVERITY_COLOURS[Severity.ACTIVE]}]{glyphs.SUB_ACTIVE} all healthy[/]"
        self._paint(
            "#header",
            f"[b $primary]crosspoint[/]  {verdict}  [$text-dim]|[/]  {count}  "
            f"{glyphs.CLOCK_MASTER} {master}  {self.backend.name}  "
            f"iface:{self.iface or 'any'}  {age}s ago"
        )

    def _paint(self, selector: str, markup: str) -> None:
        """Update a Static, tolerating a tree that is still being built.

        Every paint here is message-driven, and messages outrun mounting:
        `Matrix.__init__` posts `CursorMoved` while the routing pane is still
        composing, so its status line may not exist yet. Skipping is right — the
        refresh at the end of mount paints everything again.
        """
        for widget in self.query(selector).results(Static):
            widget.update(markup)

    def _paint_device(self) -> None:
        devices = self.snapshot.devices
        if not devices:
            self._paint("#devices-body", "no devices")
            return
        device = next((d for d in devices if d.name == self._selected), devices[0])
        mark, colour = _clock_glyph(device)
        state = "online" if device.online else "OFFLINE"
        if device.is_locked:
            state = f"{state} {glyphs.LOCK} locked"
        maker = f"{device.manufacturer} " if device.manufacturer else ""
        formats = sorted(
            {c.format for c in device.tx_channels + device.rx_channels if c.format}
        )
        lines = [
            f"[b]{device.name}[/b]   [{colour}]{state}[/]",
            f"model        {maker}{device.model or '-'}"
            + (f"  [$text-dim]({device.dante_model_id})[/]" if device.dante_model_id else ""),
            f"id           {device.id}" + (f"  mac {device.mac_address}" if device.mac_address else ""),
            f"addresses    {', '.join(device.addresses) or '-'}",
        ]
        versions = "   ".join(
            f"{label} {value}"
            for label, value in (("fw", device.firmware_version), ("sw", device.software_version))
            if value
        )
        if versions:
            lines.append(f"versions     {versions}")
        # Same units as the table above — two spellings of one number is a bug
        # report waiting to happen.
        latency = _latency(device.latency_us)
        if device.min_latency_us is not None or device.max_latency_us is not None:
            lo = _latency(device.min_latency_us)
            hi = _latency(device.max_latency_us)
            latency = f"{latency} (device allows {lo} to {hi})"
        lines.append(f"{glyphs.SAMPLE_RATE} sample rate  {_rate(device.sample_rate)}")
        lines.append(f"{glyphs.LATENCY} latency      {latency}")
        channels = f"{len(device.tx_channels)} tx / {len(device.rx_channels)} rx"
        # Flows and network count say how full a device is; formats say what it speaks.
        extras = []
        if device.tx_flow_count is not None or device.rx_flow_count is not None:
            tx = device.tx_flow_count if device.tx_flow_count is not None else "?"
            rx = device.rx_flow_count if device.rx_flow_count is not None else "?"
            extras.append(f"{tx}/{rx} flows")
        if device.num_networks is not None:
            extras.append(f"{device.num_networks} net{'s' if device.num_networks != 1 else ''}")
        if formats:
            extras.append("/".join(formats))
        if extras:
            channels += f"  [$text-dim]{'  '.join(extras)}[/]"
        lines.append(f"channels     {channels}")
        if device.clock:
            ppm = device.clock.frequency_offset_ppm
            lines.append(
                f"{mark} clock        {device.clock.role}, {device.clock.sync_state}"
                + (f", {ppm:+.1f} ppm" if ppm is not None else "")
                + (", external sync" if device.clock.external_sync else "")
            )
        if device.aes67_configured is not None:
            aes = "on" if device.aes67_configured else "off"
            if device.aes67_pending_reboot:
                # Configured and active disagree until the device is rebooted.
                warn = SEVERITY_COLOURS[Severity.WARN]
                active = "on" if device.aes67_current else "off"
                lines.append(f"aes67        [{warn}]{aes} configured, {active} active — needs reboot[/]")
            else:
                lines.append(f"aes67        {aes}")
        self._paint("#devices-body", "\n".join(lines))

    # ---- actions --------------------------------------------------------

    def action_tab(self, name: str) -> None:
        self.query_one("#tabs", TabbedContent).active = name

    def action_toggle_sidebar(self) -> None:
        self.query_one("#sidebar").toggle_class("hidden")

    def action_filter(self) -> None:
        self.action_tab("routing")
        field = self.query_one("#filter", Input)
        field.add_class("open")
        # A hidden widget cannot take focus; wait for the layout to catch up.
        self.call_after_refresh(field.focus)

    def action_clear(self) -> None:
        if self._filter_timer is not None:
            self._filter_timer.stop()
            self._filter_timer = None
        field = self.query_one("#filter", Input)
        field.value = ""
        field.remove_class("open")
        self._set_filter("")
        self.query_one("#detail").remove_class("open")
        self.query_one("#matrix", Matrix).focus()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    # ---- selection ------------------------------------------------------

    def _select(self, name: str | None) -> None:
        """One selected device, shared by the sidebar and the Devices table.

        Two lists of the same devices that disagree about which one is selected
        is exactly the kind of thing that makes a tool feel untrustworthy.

        Only the sidebar reaches this. Moving a cursor makes Textual post the
        matching Highlighted message, so if both lists synced each other they
        would echo forever — and they do not even start out agreeing, because
        the table is sorted and the sidebar is not. So the table's cursor feeds
        back through the sidebar (see `on_data_table_row_highlighted`) and only
        this one direction does the work. Every write below is a no-op when the
        value is unchanged, which is what stops the echo.

        ponytail: one-way sync plus `prevent` at fill time, rather than tracking
        which cursor move was ours. If a third list of devices ever appears, that
        bookkeeping becomes worth writing; with two it is not.
        """
        if name is None or name == self._selected:
            return
        self._selected = name
        self._paint_device()
        for table in self.query("#device-table").results(DataTable):
            row = next(
                (i for i, r in enumerate(table.ordered_rows) if r.key.value == name), None
            )
            if row is not None:
                table.move_cursor(row=row)
        # Park the grid on the same device, so switching to Routing shows the
        # device you were just reading about rather than wherever you left off.
        self._move_matrix_to(name)

    def _highlight_in_sidebar(self, name: str) -> None:
        for sidebar in self.query(Sidebar).results(Sidebar):
            for index, item in enumerate(sidebar.children):
                if item.name == name:
                    sidebar.index = index
                    return

    def _move_matrix_to(self, name: str) -> None:
        matrix = next(iter(self.query("#matrix").results(Matrix)), None)
        if matrix is None:
            return
        for index, row in enumerate(matrix.rows):
            if row.device == name and row.channel is None:
                matrix.cursor_row = index
                break
        for index, col in enumerate(matrix.cols):
            if col.device == name:
                matrix.cursor_col = index
                break

    # ---- events ---------------------------------------------------------

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None:
            self._select(event.item.name)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Moving the table cursor moves the sidebar, which does the rest.

        Deliberately not `_select`: routing both lists through one direction is
        what keeps the two cursors from echoing each other indefinitely.
        """
        if event.data_table.id == "device-table":
            self._highlight_in_sidebar(cast(str, event.row_key.value))

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        # Land focus where the keys do something: j/k/h/l belong to the matrix.
        if event.pane.id == "routing":
            self.query_one("#matrix", Matrix).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        # A rebuild is ~70 ms at 200 devices. Running it per keystroke makes the
        # filter box feel broken; running it once the typing stops does not.
        if self._filter_timer is not None:
            self._filter_timer.stop()
        value = event.value
        self._filter_timer = self.set_timer(FILTER_DEBOUNCE, lambda: self._set_filter(value))

    def _set_filter(self, value: str) -> None:
        self._filter_timer = None
        self.query_one("#matrix", Matrix).filter_text = value

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self.query_one("#matrix", Matrix).focus()

    def on_matrix_cursor_moved(self, event: Matrix.CursorMoved) -> None:
        self._paint_matrix_status(event.matrix)

    def _paint_matrix_status(self, matrix: Matrix) -> None:
        row, col = matrix.row, matrix.col
        if row is None or col is None:
            self._paint("#matrix-status", "[$text-dim]nothing matches the filter[/]")
            return
        rx = row.device if row.channel is None else f"{row.device} / {row.channel}"
        tx = col.device if col.channel is None else f"{col.device} / {col.channel}"
        problems = matrix.problem_count
        colour = SEVERITY_COLOURS[Severity.ERROR] if problems else "$text-muted"
        floor = {None: "all", Severity.WARN: "problems", Severity.ERROR: "errors"}[matrix.severity_floor]
        self._paint(
            "#matrix-status",
            f"rx [b]{rx}[/b]   ←   tx [b]{tx}[/b]"
            f"   [$text-dim]row {matrix.cursor_row + 1}/{len(matrix.rows)}"
            f"  col {matrix.cursor_col + 1}/{len(matrix.cols)}[/]"
            # "cells", not "problems": the header counts bad subscriptions, and a
            # collapsed cell can hide sixty of them. Two numbers that disagree
            # need to say what they are counting.
            f"   [{colour}]{problems} problem cells[/] [$text-dim]({floor})[/]"
        )

    def on_matrix_cell_selected(self, event: Matrix.CellSelected) -> None:
        row, col, cell = event.row, event.col, event.cell
        source = row.device if row.channel is None else f"{row.channel}@{row.device}"
        target = col.device if col.channel is None else f"{col.channel}@{col.device}"
        heading = f"[b]{source}[/b]  ←  [b]{target}[/b]"
        if cell is None:
            lines = [heading, "[$text-muted]nothing is routed here[/]"]
        else:
            sub = cell.example
            colour = SEVERITY_COLOURS[cell.severity]
            glyph = glyphs.severity_glyph(cell.severity.value)
            lines = [heading]
            if cell.count > 1:
                lines.append(f"[$text-muted]{cell.breakdown()} subscriptions under this cell[/]")
                lines.append(f"[b]{sub.rx_channel}@{sub.rx_device}[/b]  ←  [b]{sub.tx_channel}@{sub.tx_device}[/b]")
            # Sentence first, protocol identifier second: "Mismatched clock
            # domains" is what someone can act on. CLOCK_DOMAIN/0x001b is what
            # they quote to whoever they escalate to.
            lines += [
                f"[{colour}]{glyph} {sub.status.label or sub.status.name}[/]",
                f"[$text-dim]{sub.status.name}  0x{sub.status.code:04x}  {sub.severity.value}[/]",
            ]
            if sub.reason:
                lines.append(f"[{colour}]reason: {sub.reason}[/]")
        for detail in self.query("#detail").results(Static):
            detail.update("\n".join(lines))
            detail.add_class("open")

    def action_jump_to_device(self, name: str) -> None:
        """Show me this device's routing — an explicit 'take me there', so this
        one does change tab. `_select` only parks the cursor."""
        matrix = self.query_one("#matrix", Matrix)
        matrix.filter_text = ""
        self._select(name)
        self._move_matrix_to(name)  # _select is a no-op when already selected
        self.action_tab("routing")
        matrix.focus()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Click a column header to sort by it, click again to reverse — the one
        table convention everybody already knows."""
        table_id = event.data_table.id
        if table_id in self._sort:
            self._toggle_sort(table_id, str(event.column_key))
