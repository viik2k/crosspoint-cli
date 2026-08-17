"""One snapshot per tab, plus the states worth rendering well.

The fixture deliberately contains a broken subscription, a clock slave with a
large offset, an offline device and a 64-channel device — if any of those stop
rendering, a snapshot moves.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from crosspoint.app import CrosspointApp, _help_text
from crosspoint.backends import MockBackend
from crosspoint.matrix import Matrix
from crosspoint.model import Severity, status

SIZE = (120, 34)


@pytest.fixture
def app() -> CrosspointApp:
    return CrosspointApp(MockBackend(), iface="eth1")


def test_devices_tab(snap_compare, app):
    assert snap_compare(app, press=["1"], terminal_size=SIZE)


def test_routing_overview(snap_compare, app):
    """Default view: every device block collapsed, one screen for the whole network."""
    assert snap_compare(app, press=["2"], terminal_size=SIZE)


def test_routing_expanded(snap_compare, app):
    """Expand device-c-amp — the block whose feed is in the wrong clock domain."""
    assert snap_compare(app, press=["2", "j", "j", "space"], terminal_size=SIZE)


def test_routing_next_problem(snap_compare, app):
    """`n` is how you cross a large matrix: jump straight to a broken cell."""
    assert snap_compare(app, press=["2", "n", "n", "enter"], terminal_size=SIZE)


def test_routing_expand_all(snap_compare, app):
    assert snap_compare(app, press=["2", "E", "G"], terminal_size=SIZE)


def test_clock_tab(snap_compare, app):
    assert snap_compare(app, press=["3"], terminal_size=SIZE)


def test_events_tab(snap_compare, app):
    assert snap_compare(app, press=["4"], terminal_size=SIZE)


def test_help_overlay(snap_compare, app):
    assert snap_compare(app, press=["question_mark"], terminal_size=SIZE)


# ---- non-visual checks the snapshots cannot make ------------------------


def test_status_codes_mirror_netaudio():
    """Spot-check the codes crosspoint claims to have copied from netaudio."""
    assert status(9).name == "DYNAMIC"
    assert status(9).severity is Severity.ACTIVE
    assert status(27).name == "CLOCK_DOMAIN"
    assert status(27).severity is Severity.ERROR
    assert status(0).severity is Severity.OK
    # A code netaudio never documented must not silently read as healthy.
    assert status(4242).severity is Severity.ERROR


def test_fixture_covers_the_interesting_states():
    import asyncio

    snap = asyncio.run(MockBackend().snapshot())
    assert any(not d.online for d in snap.devices)
    assert any(len(d.rx_channels) == 64 for d in snap.devices)
    assert any(s.severity is Severity.ERROR for s in snap.subscriptions)
    assert any(s.severity is Severity.WARN for s in snap.subscriptions)
    drifting = [
        d for d in snap.devices
        if d.clock and d.clock.role == "slave" and abs(d.clock.frequency_offset_ppm or 0) > 5
    ]
    assert drifting, "fixture must contain a clock slave with a large offset"
    # The netaudio identity/capability fields must actually round-trip.
    assert any(d.firmware_version for d in snap.devices)
    assert any(d.software_version for d in snap.devices)
    assert any(d.is_locked for d in snap.devices)
    assert any(d.max_latency_us is not None for d in snap.devices)
    assert any(d.num_networks == 2 for d in snap.devices)


def test_cell_breakdown_is_worst_first():
    import asyncio

    snap = asyncio.run(MockBackend().snapshot())
    matrix = Matrix(snap)
    cell = next(cell for row in matrix.rows for cell in row.cells.values() if cell.count > 1)
    parts = cell.breakdown().split(", ")
    assert parts == sorted(parts, key=lambda part: {"error": 0, "warn": 1, "active": 2, "ok": 3}[part.split()[1]])


def test_deferred_ui_features_are_wired():
    import asyncio

    from textual.command import CommandPalette
    from textual.widgets import DataTable

    async def run():
        app = CrosspointApp(MockBackend())
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            table = app.query_one("#clock-table", DataTable)
            assert app._sort["clock-table"] == ("role", False)
            app._toggle_sort("clock-table", "device")
            assert app._sort["clock-table"] == ("device", False)
            assert table.columns[cast(Any, "device")].label.plain.endswith("^")
            app._toggle_sort("clock-table", "device")  # same column reverses
            assert app._sort["clock-table"] == ("device", True)
            app._append_events((*app.snapshot.events, "new event"))
            app._append_events((*app.snapshot.events, "new event"))
            assert app._event_source[-1] == "new event"
            assert len(app._event_source) == len(app.snapshot.events) + 1
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            await pilot.press("escape")
            app.action_jump_to_device("device-e-io")
            row = app.query_one("#matrix", Matrix).row
            assert row is not None and row.device == "device-e-io"

    asyncio.run(run())
    help_text = _help_text()
    assert "ctrl+p" in help_text
    assert "cycle_severity" not in help_text


def test_the_mouse_drives_the_matrix():
    """Nothing may be keyboard-only: a click has to move, inspect and fold.

    The coordinate maths crosses a border, a sticky gutter and two scroll
    offsets, which is exactly the sort of arithmetic that quietly goes wrong.
    """
    import asyncio

    from textual.widgets import Static

    from crosspoint.matrix import CELL_W, GUTTER, HEADER_H

    BORDER = 1  # Matrix has border-left

    async def run():
        app = CrosspointApp(MockBackend())
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("2")
            await pilot.pause()
            matrix = app.query_one("#matrix", Matrix)

            # A cell: moves the cursor there and opens the detail panel.
            await pilot.click(
                Matrix, offset=(BORDER + GUTTER + 2 * CELL_W, HEADER_H + 1)
            )
            await pilot.pause()
            assert (matrix.cursor_row, matrix.cursor_col) == (1, 2)
            assert "open" in app.query_one("#detail", Static).classes

            # The gutter is the tree control: clicking a device name folds it.
            rows_before = len(matrix.rows)
            await pilot.click(Matrix, offset=(BORDER + 3, HEADER_H + 1))
            await pilot.pause()
            assert len(matrix.rows) > rows_before, "gutter click should expand"
            await pilot.click(Matrix, offset=(BORDER + 3, HEADER_H + 1))
            await pilot.pause()
            assert len(matrix.rows) == rows_before, "and collapse again"

            # The TX band is the other tree control.
            cols_before = len(matrix.cols)
            await pilot.click(Matrix, offset=(BORDER + GUTTER, 0))
            await pilot.pause()
            assert len(matrix.cols) > cols_before, "band click should expand a column"

    asyncio.run(run())


def test_one_selected_device_across_both_lists():
    """The sidebar and the Devices table must never disagree."""
    import asyncio

    from textual.widgets import DataTable

    from crosspoint.app import Sidebar

    async def run():
        app = CrosspointApp(MockBackend())
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            table = app.query_one("#device-table", DataTable)
            table.move_cursor(row=3)
            await pilot.pause()
            picked = str(table.ordered_rows[3].key.value)
            assert app._selected == picked
            sidebar = app.query_one(Sidebar)
            assert sidebar.highlighted_child is not None
            assert sidebar.highlighted_child.name == picked
            # And the grid is already parked there.
            row = app.query_one("#matrix", Matrix).row
            assert row is not None and row.device == picked

    asyncio.run(run())
