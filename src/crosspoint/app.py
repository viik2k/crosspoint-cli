"""The TUI. Never imports a backend — one is injected at startup."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
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
from .theme import PITWALL, SEVERITY_COLOURS

TABS = ["devices", "routing", "clock", "events"]
FILTER_DEBOUNCE = 0.15  # seconds of quiet before the matrix rebuilds

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
    lines = ["[b]crosspoint[/b] — unofficial read-only TUI for Dante and AES67", ""]
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
        self.index = 0

    def action_last(self) -> None:
        self.index = max(0, len(self) - 1)


class CrosspointApp(App[None]):
    CSS: ClassVar[str] = """
    Screen { background: $background; }

    /* 1 row means 1 row: a border here would eat the only content line. */
    #header { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    #body { height: 1fr; }

    #sidebar {
        width: 28; background: $surface; border-right: solid $border;
        padding: 0;
    }
    #sidebar.hidden { display: none; }
    Sidebar { background: $surface; }
    Sidebar > ListItem { background: $surface; padding: 0 0 0 1; }
    Sidebar > ListItem.--highlight { background: $surface; border-left: solid $primary; padding: 0; }
    Sidebar:focus > ListItem.--highlight { background: $surface; }

    TabbedContent { width: 1fr; height: 1fr; }
    TabbedContent > ContentSwitcher { height: 1fr; }
    Tabs { background: $surface; }
    Tabs > #tabs-list { background: $surface; }
    TabPane { padding: 0 1; background: $background; }

    #devices-body { padding: 1 1; }

    /* Border is always present so focus does not reflow the grid. */
    Matrix { height: 1fr; background: $background; border-left: solid $border; }
    Matrix:focus { border-left: solid $border-focus; }

    /* Always-on: at 200 devices you must never have to guess where you are. */
    #matrix-status { height: 1; background: $panel; color: $text-muted; padding: 0 1; }

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
        Binding("ctrl+p", "command_palette", "Commands"),
        Binding("backslash", "toggle_sidebar", "Sidebar"),
        Binding("slash", "filter", "Filter"),
        Binding("escape", "clear", "Clear"),
        Binding("r", "refresh", "Refresh"),
        Binding("s", "sort_clock", "Sort clock"),
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
        self._clock_sort: tuple[str, bool] = ("role", False)

    # ---- layout ---------------------------------------------------------

    @override
    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="body"):
            with VerticalScroll(id="sidebar"):
                yield Sidebar()
            with TabbedContent(id="tabs"):
                with TabPane("1 Devices", id="devices"):
                    yield VerticalScroll(Static(id="devices-body"))
                with TabPane("2 Routing", id="routing"), Vertical():
                    yield Matrix(id="matrix")
                    yield Static(id="matrix-status")
                    yield Input(placeholder="filter rows; tx: filters columns", id="filter")
                    yield Static(id="detail")
                with TabPane("3 Clock", id="clock"):
                    yield DataTable(id="clock-table", cursor_type="row", zebra_stripes=False)
                with TabPane("4 Events", id="events"):
                    yield VerticalScroll(RichLog(id="events-body", markup=False, wrap=True))
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#clock-table", DataTable)
        for key, label, width in self._clock_columns:
            table.add_column(label, key=key, width=width)
        await self.action_refresh()

    # ---- data -----------------------------------------------------------

    async def action_refresh(self) -> None:
        self.snapshot = await self.backend.snapshot()
        self._refreshed_at = time.monotonic()
        matrix = self.query_one("#matrix", Matrix)
        matrix.load(self.snapshot)
        self._paint_matrix_status(matrix)
        self._fill_sidebar()
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
        the selected index — devices come and go between refreshes."""
        sidebar = self.query_one(Sidebar)
        selected = self._selected_device_name()
        sidebar.clear()
        broken = self._problem_counts()  # once, not once per device
        for device in self.snapshot.devices:
            sidebar.append(
                ListItem(Label(self._sidebar_row(device, broken.get(device.name, 0))), name=device.name)
            )
        restored = next(
            (i for i, d in enumerate(self.snapshot.devices) if d.name == selected), 0
        )
        sidebar.index = min(restored, max(0, len(self.snapshot.devices) - 1))

    def _selected_device_name(self) -> str | None:
        sidebar = self.query_one(Sidebar)
        item = sidebar.highlighted_child
        return item.name if item is not None else None

    def _problem_counts(self) -> dict[str, int]:
        """WARN/ERROR subscriptions per RX device, for the sidebar."""
        counts: dict[str, int] = {}
        for sub in self.snapshot.subscriptions:
            if sub.severity in (Severity.WARN, Severity.ERROR):
                counts[sub.rx_device] = counts.get(sub.rx_device, 0) + 1
        return counts

    def _sidebar_row(self, device: Device, broken: int) -> str:
        if device.online:
            mark, colour = glyphs.DEVICE_ONLINE, SEVERITY_COLOURS[Severity.ACTIVE]
        else:
            mark, colour = glyphs.DEVICE_OFFLINE, SEVERITY_COLOURS[Severity.ERROR]
        clock_mark, clock_colour = _clock_glyph(device)
        name_style = f"[{colour}]" if not device.online else ""
        name_end = "[/]" if not device.online else ""
        # A bare count is the cheapest way to see which of 200 devices needs you.
        tally = f"[{SEVERITY_COLOURS[Severity.ERROR]}]{broken:>3}[/]" if broken else "   "
        return (
            f"[{colour}]{mark}[/] {name_style}{device.name[:16]:<16}{name_end} "
            f"[{clock_colour}]{clock_mark}[/]{tally}"
        )

    def _fill_clock(self) -> None:
        table = self.query_one("#clock-table", DataTable)
        table.clear()
        key, reverse = self._clock_sort
        ordered = sorted(self.snapshot.devices, key=lambda d: self._clock_value(d, key), reverse=reverse)
        for column_key, label, _ in self._clock_columns:
            if column_key == key:
                label += " v" if reverse else " ^"
            table.columns[cast(Any, column_key)].label = Text(label)
        for device in ordered:
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

    _clock_columns = (
        ("device", "device", 18),
        ("role", "role", 10),
        ("sync", "sync", 10),
        ("offset", "offset ppm", 12),
        ("external", "ext sync", 9),
        ("source", "source", 18),
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

    def _sort_clock(self, column: str) -> None:
        if column not in {candidate for candidate, _, _ in self._clock_columns}:
            return
        current, reverse = self._clock_sort
        self._clock_sort = (column, not reverse) if column == current else (column, False)
        self._fill_clock()

    def action_sort_clock(self) -> None:
        if self.query_one("#tabs", TabbedContent).active != "clock":
            return
        table = self.query_one("#clock-table", DataTable)
        if table.cursor_column is not None:
            self._sort_clock(str(table.ordered_columns[table.cursor_column].key))

    def _paint_header(self) -> None:
        age = int(time.monotonic() - self._refreshed_at)
        master = self.snapshot.clock_master or "none"
        offline = sum(1 for d in self.snapshot.devices if not d.online)
        count = f"{len(self.snapshot.devices)} devices"
        if offline:
            count += f" ({offline} offline)"
        self.query_one("#header", Static).update(
            f"[b $primary]crosspoint[/]  {self.backend.name}  iface:{self.iface or 'any'}  "
            f"{count}  {glyphs.CLOCK_MASTER} {master}  {age}s ago"
        )

    def _paint_device(self) -> None:
        sidebar = self.query_one(Sidebar)
        devices = self.snapshot.devices
        if not devices:
            self.query_one("#devices-body", Static).update("no devices")
            return
        device = devices[min(sidebar.index or 0, len(devices) - 1)]
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
        latency = str(device.latency_us or "-")
        if device.min_latency_us is not None or device.max_latency_us is not None:
            lo = device.min_latency_us if device.min_latency_us is not None else "?"
            hi = device.max_latency_us if device.max_latency_us is not None else "?"
            latency = f"{latency} us (device allows {lo}-{hi} us)"
        else:
            latency = f"{latency} us"
        lines.append(
            f"{glyphs.SAMPLE_RATE} sample rate  {device.sample_rate or '-'} Hz"
        )
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
        self.query_one("#devices-body", Static).update("\n".join(lines))

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

    # ---- events ---------------------------------------------------------

    def on_list_view_highlighted(self, _: ListView.Highlighted) -> None:
        self._paint_device()

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
            self.query_one("#matrix-status", Static).update("[$text-dim]nothing matches the filter[/]")
            return
        rx = row.device if row.channel is None else f"{row.device} / {row.channel}"
        tx = col.device if col.channel is None else f"{col.device} / {col.channel}"
        problems = matrix.problem_count
        colour = SEVERITY_COLOURS[Severity.ERROR] if problems else "$text-muted"
        floor = {None: "all", Severity.WARN: "problems", Severity.ERROR: "errors"}[matrix.severity_floor]
        self.query_one("#matrix-status", Static).update(
            f"rx [b]{rx}[/b]   ←   tx [b]{tx}[/b]"
            f"   [$text-dim]row {matrix.cursor_row + 1}/{len(matrix.rows)}"
            f"  col {matrix.cursor_col + 1}/{len(matrix.cols)}[/]"
            f"   [{colour}]{problems} problems[/] [$text-dim]({floor})[/]"
        )

    def on_matrix_cell_selected(self, event: Matrix.CellSelected) -> None:
        detail = self.query_one("#detail", Static)
        row, col, cell = event.row, event.col, event.cell
        source = row.device if row.channel is None else f"{row.channel}@{row.device}"
        target = col.device if col.channel is None else f"{col.channel}@{col.device}"
        heading = f"[b]{source}[/b]  ←  [b]{target}[/b]"
        if cell is None:
            detail.update(f"{heading}\n[$text-muted]no subscription[/]")
        else:
            sub = cell.example
            colour = SEVERITY_COLOURS[cell.severity]
            glyph = glyphs.severity_glyph(cell.severity.value)
            lines = [heading]
            if cell.count > 1:
                lines.append(f"[$text-muted]{cell.breakdown()} subscriptions under this cell[/]")
                lines.append(f"[b]{sub.rx_channel}@{sub.rx_device}[/b]  ←  [b]{sub.tx_channel}@{sub.tx_device}[/b]")
            lines += [
                f"[{colour}]{glyph} {sub.status.name}[/] (0x{sub.status.code:04x})  {sub.severity.value}",
                f"[$text-muted]{sub.status.label or 'no label in netaudio for this code'}[/]",
            ]
            if sub.reason:
                lines.append(f"[{colour}]reason: {sub.reason}[/]")
            detail.update("\n".join(lines))
        detail.add_class("open")

    def action_jump_to_device(self, name: str) -> None:
        """Put the matrix cursor on a device summary selected in the palette."""
        matrix = self.query_one("#matrix", Matrix)
        matrix.filter_text = ""
        for index, row in enumerate(matrix.rows):
            if row.device == name and row.channel is None:
                matrix.cursor_row = index
                break
        for index, col in enumerate(matrix.cols):
            if col.device == name:
                matrix.cursor_col = index
                break
        self.action_tab("routing")
        matrix.focus()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.data_table.id == "clock-table":
            self._sort_clock(str(event.column_key))
