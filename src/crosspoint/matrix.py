"""The routing matrix.

Rows are RX channels grouped by RX device; columns are TX channels grouped by TX
device. Only the visible window is ever rendered.

At production scale — 200 devices x 64 channels — the full grid is 12800 x 12800,
and it is 99.99% empty: an RX channel subscribes to at most one TX channel, so
there are at most 12800 filled cells in 164 million. Rendering that is cheap
(3 ms/frame, virtualised) but *crossing* it is not: 12800 keypresses per axis.

So device blocks are collapsed by default. A collapsed block is one row (or one
column) whose cell shows the worst severity of every subscription underneath it,
which is the view you actually want first: which device pairs are unhealthy.
Expand a block to get its channels. Navigation is built around jumping — to a
block, to a page, to the next problem — not scrolling.

ponytail: hand-rolled `render_line` instead of DataTable. DataTable's
`_render_line_in_row` loops over every column in `ordered_columns` with no
horizontal culling, and its row cache is keyed on cursor position, so one cursor
move re-renders visible_rows x n_columns cells. Measured at 20 devices x 64
channels, repainting the viewport after a cursor move: DataTable 4124 ms, this
widget 2.9 ms. It also has exactly one header row, which cannot carry the sticky
TX-device band. Upgrade path: if Textual ever lands horizontally-virtualised
DataTable columns, this file collapses into it.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from rich.segment import Segment
from rich.style import Style
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip

from . import glyphs
from .model import Severity, Snapshot, Subscription
from .theme import ACCENT, BACKGROUND, PANEL, PRIMARY, SEVERITY_COLOURS, TEXT_DIM, TEXT_MUTED

CELL_W = 2  # glyph + space. Fixed: strict column alignment everywhere.
GUTTER = 20  # fixed first column: fold marker, RX device / channel name
HEADER_H = 2  # line 0: TX device band, line 1: TX channel numbers

_RANK = {Severity.OK: 0, Severity.ACTIVE: 1, Severity.WARN: 2, Severity.ERROR: 3}
_PROBLEM = (Severity.WARN, Severity.ERROR)

ColKey = tuple[str, str | None]  # (tx device, tx channel or None when collapsed)


@dataclass(slots=True)
class Cell:
    """What sits at one crosspoint. More than one subscription only under a
    collapsed block, where `counts` keeps the tally per severity — otherwise a
    cell covering 60 healthy and 1 broken looks identical to one covering 1."""

    severity: Severity  # the worst present
    example: Subscription  # the worst-severity subscription in this cell
    counts: list[int] = field(default_factory=lambda: [0, 0, 0, 0])  # indexed by _RANK

    def add(self, sub: Subscription) -> None:
        self.counts[_RANK[sub.severity]] += 1
        if _RANK[sub.severity] > _RANK[self.severity]:
            self.severity, self.example = sub.severity, sub

    @property
    def count(self) -> int:
        return sum(self.counts)

    def breakdown(self) -> str:
        """e.g. "58 active, 2 warn, 1 error" — worst first."""
        return ", ".join(
            f"{self.counts[_RANK[severity]]} {severity.value}"
            for severity in (Severity.ERROR, Severity.WARN, Severity.ACTIVE, Severity.OK)
            if self.counts[_RANK[severity]]
        )


@dataclass(slots=True)
class Row:
    device: str
    channel: str | None  # None => device summary row
    expanded: bool = False  # device rows only
    cells: dict[ColKey, Cell] = field(default_factory=dict)


@dataclass(slots=True)
class Col:
    device: str
    channel: str | None  # None => collapsed device column
    number: int  # 1-based position within the block, 0 when collapsed
    first: bool  # first column of its TX device block

    @property
    def key(self) -> ColKey:
        return (self.device, self.channel)


_SEV_STYLE = {
    Severity.OK: Style(color=SEVERITY_COLOURS[Severity.OK]),
    Severity.ACTIVE: Style(color=SEVERITY_COLOURS[Severity.ACTIVE]),
    Severity.WARN: Style(color=SEVERITY_COLOURS[Severity.WARN]),
    Severity.ERROR: Style(color=SEVERITY_COLOURS[Severity.ERROR], bold=True),
}
_EMPTY = Style(color=TEXT_DIM)
_MUTED = Style(color=TEXT_MUTED)
_GROUP = Style(color=ACCENT, bold=True)
_BAND = Style(color=ACCENT, bgcolor=PANEL)
_BAND_PIN = Style(color=PRIMARY, bgcolor=PANEL, bold=True)
_BAR = Style(color=PRIMARY, bold=True)
_CURSOR = Style(color=BACKGROUND, bgcolor=PRIMARY, bold=True)


class Matrix(ScrollView):
    """Virtualised, foldable subscription grid."""

    can_focus = True

    BINDINGS = [
        Binding("j,down", "move(1,0)", "Down", show=False),
        Binding("k,up", "move(-1,0)", "Up", show=False),
        Binding("h,left", "move(0,-1)", "Left", show=False),
        Binding("l,right", "move(0,1)", "Right", show=False),
        Binding("g", "top", "Top", show=False),
        Binding("G", "bottom", "Bottom", show=False),
        Binding("pagedown,ctrl+f", "page(1)", "Page down", show=False),
        Binding("pageup,ctrl+b", "page(-1)", "Page up", show=False),
        Binding("L,shift+right", "hpage(1)", "Page right", show=False),
        Binding("H,shift+left", "hpage(-1)", "Page left", show=False),
        Binding("right_square_bracket", "block(1)", "Next TX block", show=False),
        Binding("left_square_bracket", "block(-1)", "Prev TX block", show=False),
        Binding("0,home", "edge(-1)", "First column", show=False),
        Binding("dollar_sign,end", "edge(1)", "Last column", show=False),
        Binding("n", "problem(1)", "Next problem"),
        Binding("N", "problem(-1)", "Prev problem", show=False),
        Binding("p", "cycle_severity", "Problems only"),
        Binding("enter", "inspect", "Inspect"),
        Binding("space", "fold_row", "Fold RX"),
        Binding("t", "fold_column", "Fold TX"),
        Binding("E", "fold_all(False)", "Expand all", show=False),
        Binding("C", "fold_all(True)", "Collapse all", show=False),
    ]

    cursor_row: reactive[int] = reactive(0)
    cursor_col: reactive[int] = reactive(0)
    filter_text: reactive[str] = reactive("")
    severity_floor: reactive[Severity | None] = reactive(None)

    class CellSelected(Message):
        """Enter pressed on a channel row."""

        def __init__(self, row: Row, col: Col, cell: Cell | None) -> None:
            super().__init__()
            self.row, self.col, self.cell = row, col, cell

    class CursorMoved(Message):
        """Cursor or fold state changed; the status line should repaint."""

        def __init__(self, matrix: Matrix) -> None:
            super().__init__()
            self.matrix = matrix

    def __init__(self, snapshot: Snapshot | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rows: list[Row] = []
        self.cols: list[Col] = []
        self._expanded_rx: set[str] = set()
        self._expanded_tx: set[str] = set()
        self._problems: list[tuple[int, int]] = []
        self._snapshot = snapshot or Snapshot()
        self.load(self._snapshot)

    # ---- data -----------------------------------------------------------

    def load(self, snapshot: Snapshot) -> None:
        """Take a new snapshot, keeping fold state and the cursor's *identity*.

        Row indices shift whenever a device appears or drops off, so a refresh
        that restored the index would silently move you to a different device.
        """
        row, col = self.row, self.col
        anchor = (
            (row.device, row.channel) if row else None,
            (col.device, col.channel) if col else None,
        )
        self._snapshot = snapshot
        self._rebuild()
        self._restore(*anchor)

    def _restore(self, row_key: tuple | None, col_key: tuple | None) -> None:
        if row_key is not None:
            for i, row in enumerate(self.rows):
                if (row.device, row.channel) == row_key:
                    self.cursor_row = i
                    break
        if col_key is not None:
            for i, col in enumerate(self.cols):
                if (col.device, col.channel) == col_key:
                    self.cursor_col = i
                    break

    def _needles(self) -> tuple[list[str], list[str]]:
        """Parse the filter box. Bare words narrow rows; `tx:` narrows columns.

        Filtering both axes off one word is what makes a big matrix unusable —
        typing an RX device name would empty the column axis entirely.
        """
        rx: list[str] = []
        tx: list[str] = []
        for token in self.filter_text.lower().split():
            if token.startswith("tx:"):
                if token[3:]:
                    tx.append(token[3:])
            elif token.startswith("rx:"):
                if token[3:]:
                    rx.append(token[3:])
            elif token:
                rx.append(token)
        return rx, tx

    def _rebuild(self) -> None:
        rx_needles, tx_needles = self._needles()

        def keep(needles: list[str], device: str, channels: tuple) -> tuple:
            """Channels of `device` passing `needles`; a needle may match either name.

            The empty and whole-device-matches cases skip the per-channel scan
            entirely — at 200 devices that scan is 25600 comparisons, and it used
            to run on every keystroke even with an empty filter box.
            """
            if not needles:
                return channels
            name = device.lower()
            if all(n in name for n in needles):
                return channels
            return tuple(
                c for c in channels
                if all(n in name or n in c.name.lower() for n in needles)
            )

        rows: list[Row] = []
        index: dict[tuple[str, str | None], tuple[int, Row]] = {}
        for device in self._snapshot.devices:
            channels = keep(rx_needles, device.name, device.rx_channels)
            if not channels:
                continue
            expanded = device.name in self._expanded_rx
            row = Row(device.name, None, expanded)
            index[(device.name, None)] = (len(rows), row)
            rows.append(row)
            if expanded:
                for c in channels:
                    child = Row(device.name, c.name)
                    index[(device.name, c.name)] = (len(rows), child)
                    rows.append(child)

        cols: list[Col] = []
        col_pos: dict[ColKey, int] = {}
        for device in self._snapshot.devices:
            channels = keep(tx_needles, device.name, device.tx_channels)
            if not channels:
                continue
            if device.name in self._expanded_tx:
                for i, c in enumerate(channels):
                    cols.append(Col(device.name, c.name, i + 1, i == 0))
            else:
                cols.append(Col(device.name, None, 0, True))
        for i, col in enumerate(cols):
            col_pos[col.key] = i

        # One pass over the subscriptions, never over the grid — the grid is
        # 164 million cells at production scale and 99.99% of it is empty.
        # Problem hits are collected here rather than by rescanning the cells
        # afterwards, and held as objects rather than indices so that the
        # severity filter below can renumber them without a second scan.
        hits: set[tuple[int, ColKey, Severity]] = set()
        for sub in self._snapshot.subscriptions:
            key: ColKey = (
                sub.tx_device,
                sub.tx_channel if sub.tx_device in self._expanded_tx else None,
            )
            if key not in col_pos:
                continue
            problem = sub.severity in _PROBLEM
            for row_key in ((sub.rx_device, None), (sub.rx_device, sub.rx_channel)):
                found = index.get(row_key)
                if found is None:
                    continue
                row_index, row = found
                cell = row.cells.get(key)
                if cell is None:
                    cell = row.cells[key] = Cell(sub.severity, sub)
                cell.add(sub)
                if problem:
                    hits.add((row_index, key, sub.severity))

        floor = self.severity_floor
        if floor is not None:
            # Show only the rows and columns that still have something wrong.
            # At 200 devices this is the difference between reading 200 rows and
            # reading the 30 that need you.
            rank = _RANK[floor]
            hits = {hit for hit in hits if _RANK[hit[2]] >= rank}
            keep_rows = {hit[0] for hit in hits}
            keep_cols = {hit[1] for hit in hits}
            renumber = {old: new for new, old in enumerate(sorted(keep_rows))}
            rows = [rows[i] for i in sorted(keep_rows)]
            cols = [c for c in cols if c.key in keep_cols]
            # Dropping columns can strip a block's opening column, and `first`
            # drives both the band labels and `[` / `]`.
            seen: set[str] = set()
            for c in cols:
                c.first = c.device not in seen
                seen.add(c.device)
            col_pos = {c.key: i for i, c in enumerate(cols)}
            hits = {(renumber[r], key, severity) for r, key, severity in hits}

        self.rows, self.cols = rows, cols
        self._problems = sorted({(r, col_pos[key]) for r, key, _ in hits})
        self.virtual_size = Size(GUTTER + len(cols) * CELL_W, len(rows) + HEADER_H)
        self.cursor_row = max(0, min(self.cursor_row, len(rows) - 1))
        self.cursor_col = max(0, min(self.cursor_col, len(cols) - 1))
        self.refresh()
        self.post_message(self.CursorMoved(self))

    def watch_filter_text(self) -> None:
        self._rebuild()

    def watch_severity_floor(self) -> None:
        self._rebuild()
        self._scroll_to_cursor()

    def action_cycle_severity(self) -> None:
        """Everything -> problems -> errors only -> everything."""
        self.severity_floor = {
            None: Severity.WARN,
            Severity.WARN: Severity.ERROR,
            Severity.ERROR: None,
        }[self.severity_floor]

    # ---- current position ----------------------------------------------

    @property
    def row(self) -> Row | None:
        return self.rows[self.cursor_row] if self.rows else None

    @property
    def col(self) -> Col | None:
        return self.cols[self.cursor_col] if self.cols else None

    @property
    def cell(self) -> Cell | None:
        row, col = self.row, self.col
        return row.cells.get(col.key) if row and col else None

    @property
    def problem_count(self) -> int:
        return len(self._problems)

    # ---- folding --------------------------------------------------------

    def _refold(self, keep_device: str | None = None) -> None:
        """Rebuild, then put the cursor back on `keep_device`'s summary row."""
        self._rebuild()
        if keep_device is not None:
            for i, row in enumerate(self.rows):
                if row.device == keep_device and row.channel is None:
                    self.cursor_row = i
                    break
        self._scroll_to_cursor()

    def action_inspect(self) -> None:
        """Always inspect. Folding lives on its own keys — `n` lands you on
        aggregate cells, and Enter there must show you what broke, not refold."""
        row, col = self.row, self.col
        if row is not None and col is not None:
            self.post_message(self.CellSelected(row, col, row.cells.get(col.key)))

    def action_fold_row(self) -> None:
        row = self.row
        if row is None:
            return
        self._expanded_rx ^= {row.device}
        self._refold(row.device)

    def action_fold_column(self) -> None:
        col = self.col
        if col is None:
            return
        device = col.device
        self._expanded_tx ^= {device}
        self._rebuild()
        for i, candidate in enumerate(self.cols):
            if candidate.device == device:
                self.cursor_col = i
                break
        self._scroll_to_cursor()

    def action_fold_all(self, collapse: bool) -> None:
        device = self.row.device if self.row else None
        if collapse:
            self._expanded_rx.clear()
            self._expanded_tx.clear()
        else:
            self._expanded_rx = {d.name for d in self._snapshot.devices}
            self._expanded_tx = {d.name for d in self._snapshot.devices}
        self._refold(device)

    # ---- cursor ---------------------------------------------------------

    def _set_cursor(self, row: int, col: int) -> None:
        self.cursor_row = max(0, min(row, len(self.rows) - 1))
        self.cursor_col = max(0, min(col, len(self.cols) - 1))
        self._scroll_to_cursor()

    def action_move(self, dy: int, dx: int) -> None:
        self._set_cursor(self.cursor_row + dy, self.cursor_col + dx)

    def action_page(self, direction: int) -> None:
        step = max(1, self.scrollable_content_region.height - HEADER_H - 1)
        self._set_cursor(self.cursor_row + direction * step, self.cursor_col)

    def action_hpage(self, direction: int) -> None:
        step = max(1, (self.scrollable_content_region.width - GUTTER) // CELL_W - 1)
        self._set_cursor(self.cursor_row, self.cursor_col + direction * step)

    def action_top(self) -> None:
        self._set_cursor(0, self.cursor_col)

    def action_bottom(self) -> None:
        self._set_cursor(len(self.rows) - 1, self.cursor_col)

    def action_edge(self, direction: int) -> None:
        self._set_cursor(self.cursor_row, len(self.cols) - 1 if direction > 0 else 0)

    def action_block(self, direction: int) -> None:
        """Jump to the first column of the previous/next TX device block."""
        starts = [i for i, c in enumerate(self.cols) if c.first]
        if not starts:
            return
        if direction > 0:
            i = bisect_right(starts, self.cursor_col)
            target = starts[i] if i < len(starts) else starts[-1]
        else:
            i = bisect_left(starts, self.cursor_col)
            target = starts[i - 1] if i > 0 else starts[0]
        self._set_cursor(self.cursor_row, target)

    def action_problem(self, direction: int) -> None:
        """Jump to the next/previous WARN or ERROR cell, in row-major order."""
        if not self._problems:
            self.app.bell()
            return
        here = (self.cursor_row, self.cursor_col)
        if direction > 0:
            i = bisect_right(self._problems, here)
            target = self._problems[i % len(self._problems)]
        else:
            i = bisect_left(self._problems, here)
            target = self._problems[i - 1]
        self._set_cursor(*target)

    def _scroll_to_cursor(self) -> None:
        # Scrollbars overlay the content region, so scroll maths has to use the
        # region they leave behind or the last row/column never becomes visible.
        width, height = self.scrollable_content_region.size
        if not width or not height:
            return
        x, y = self.scroll_offset
        visible_rows = max(1, height - HEADER_H)
        y = max(self.cursor_row - visible_rows + 1, min(y, self.cursor_row))
        span = max(1, width - GUTTER)
        left = self.cursor_col * CELL_W
        x = max(left + CELL_W - span, min(x, left))
        self.scroll_to(max(0, x), max(0, y), animate=False, force=True)

    def on_resize(self) -> None:
        self._scroll_to_cursor()

    def watch_cursor_row(self) -> None:
        self.refresh()
        self.post_message(self.CursorMoved(self))

    def watch_cursor_col(self) -> None:
        self.refresh()
        self.post_message(self.CursorMoved(self))

    # ---- rendering ------------------------------------------------------

    def _block_width(self, start: int) -> int:
        """Cell width of the TX device block beginning at column `start`."""
        device = self.cols[start].device
        end = start + 1
        while end < len(self.cols) and self.cols[end].device == device:
            end += 1
        return (end - start) * CELL_W

    def _visible_cols(self) -> tuple[int, int, int]:
        """(first column index, sub-cell offset, count) for the viewport."""
        scroll_x = int(self.scroll_offset.x)
        start = scroll_x // CELL_W
        offset = scroll_x % CELL_W
        span = max(0, self.size.width - GUTTER)
        count = (span + offset + CELL_W - 1) // CELL_W
        return start, offset, min(count, max(0, len(self.cols) - start))

    def _join(self, gutter: list[Segment], cells: list[Segment], offset: int) -> Strip:
        """Glue the sticky gutter to the cropped, scrolled column area."""
        span = max(0, self.size.width - GUTTER)
        body = Strip(cells).crop(offset, offset + span).extend_cell_length(span)
        return Strip.join([Strip(gutter), body]).simplify()

    def render_line(self, y: int) -> Strip:
        if y < HEADER_H:
            return self._render_header(y)
        row_index = y - HEADER_H + int(self.scroll_offset.y)
        if not (0 <= row_index < len(self.rows)):
            return Strip.blank(self.size.width)
        return self._render_row(row_index)

    def _render_header(self, line: int) -> Strip:
        start, offset, count = self._visible_cols()
        cells: list[Segment] = []

        if line == 0:
            # TX device band. A name is drawn only where its block is wide enough
            # to hold it — a collapsed block is 2 cells, and names printed there
            # would overwrite each other into mush.
            span = count * CELL_W
            buf = [" "] * span
            drawn_at_left = False
            for i in range(start, start + count):
                if not self.cols[i].first:
                    continue
                at = (i - start) * CELL_W
                name = self.cols[i].device
                if len(name) + 1 > self._block_width(i):
                    continue
                buf[at : at + len(name)] = list(name[: span - at])
                drawn_at_left |= i == start
            cells = [Segment("".join(buf), _BAND)]
            # Pin the leftmost block's name whenever the band itself does not
            # show it — scrolled off, or the block is too narrow to label.
            pinned = "" if drawn_at_left or not count else self.cols[start].device
            gutter = [Segment(f"{pinned[:GUTTER]:<{GUTTER}}", _BAND_PIN)]
        else:
            # A ruler. Collapsed columns count devices, expanded ones count
            # channels; either way you can find "col 45/200" from the status line.
            for i in range(start, start + count):
                col = self.cols[i]
                n = i + 1 if col.channel is None else col.number
                cells.append(Segment(f"{n % 100:>2}" if n == 1 or n % 5 == 0 else "  ", _MUTED))
            gutter = [Segment(f"{'RX / TX':<{GUTTER}}", _MUTED)]

        return self._join(gutter, cells, offset)

    def _render_row(self, row_index: int) -> Strip:
        row = self.rows[row_index]
        start, offset, count = self._visible_cols()
        selected = row_index == self.cursor_row

        bar = Segment("▌" if selected else " ", _BAR if selected else _EMPTY)
        if row.channel is None:
            marker = glyphs.GROUP_OPEN if row.expanded else glyphs.GROUP_CLOSED
            label = f"{marker} {row.device}"[: GUTTER - 1]
            gutter = [bar, Segment(f"{label:<{GUTTER - 1}}", _GROUP)]
        else:
            label = f"   {row.channel}"[: GUTTER - 1]
            gutter = [bar, Segment(f"{label:<{GUTTER - 1}}", _MUTED)]

        cells: list[Segment] = []
        for i in range(start, start + count):
            cell = row.cells.get(self.cols[i].key)
            if cell is None:
                glyph, style = glyphs.SUB_NONE, _EMPTY
            else:
                glyph, style = glyphs.severity_glyph(cell.severity.value), _SEV_STYLE[cell.severity]
            if selected and i == self.cursor_col:
                style = _CURSOR
            cells.append(Segment(f"{glyph} ", style))

        return self._join(gutter, cells, offset)
