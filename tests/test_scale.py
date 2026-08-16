"""Production scale: 200 devices x 64 channels.

The full grid is 12800 x 12800 and 99.99% empty. These tests guard the two things
that make that survivable — the default view is collapsed to device blocks, and
nothing walks the grid.
"""

from __future__ import annotations

import asyncio
import random
import time

import pytest

from crosspoint.app import CrosspointApp
from crosspoint.backends import parse_snapshot
from crosspoint.matrix import Matrix
from crosspoint.model import Severity

DEVICES = 200
CHANNELS = 64


class _Stub:
    """A backend that hands over an already-built snapshot."""

    name = "mock"

    def __init__(self, snapshot):
        self._snapshot = snapshot

    async def snapshot(self):
        return self._snapshot


@pytest.fixture(scope="module")
def big():
    rng = random.Random(7)
    devices = [
        {
            "name": f"rack{d // 8:02d}-dev{d % 8}",
            "id": f"{d:012x}",
            "addresses": [f"10.40.{d // 250}.{d % 250 + 1}"],
            "model": "synthetic",
            "sample_rate": 48000,
            "latency_us": 1000,
            "tx_channels": [{"index": i, "name": f"Out-{i}"} for i in range(1, CHANNELS + 1)],
            "rx_channels": [{"index": i, "name": f"In-{i}"} for i in range(1, CHANNELS + 1)],
            "clock": {"role": "master" if d == 0 else "slave", "sync_state": "locked",
                      "source": "rack00-dev0", "frequency_offset_ppm": 0.2, "external_sync": False},
            "online": True,
        }
        for d in range(DEVICES)
    ]
    names = [d["name"] for d in devices]
    subs = [
        {
            "rx_device": d["name"], "rx_channel": f"In-{c}",
            "tx_device": rng.choice(names), "tx_channel": f"Out-{rng.randint(1, CHANNELS)}",
            "status": rng.choices([9, 10, 4, 27, 1, 22, 8], [55, 20, 3, 6, 6, 3, 7])[0],
        }
        for d in devices for c in range(1, CHANNELS + 1) if rng.random() <= 0.6
    ]
    return parse_snapshot({"taken_at": 0.0, "devices": devices, "subscriptions": subs})


@pytest.fixture
def matrix(big):
    return Matrix(big)


def test_default_view_is_one_row_and_column_per_device(matrix):
    """12800 x 12800 would take 12800 keypresses to cross. 200 x 200 does not."""
    assert len(matrix.rows) == DEVICES
    assert len(matrix.cols) == DEVICES


def test_rebuild_does_not_walk_the_grid(matrix, big):
    """Cost must track the subscription count, not devices squared."""
    start = time.perf_counter()
    matrix._rebuild()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"_rebuild took {elapsed * 1000:.0f} ms"
    assert len(big.subscriptions) > 5000  # the fixture is actually big


def test_collapsed_cell_reports_the_worst_status_underneath(matrix):
    cells = [c for row in matrix.rows for c in row.cells.values()]
    assert any(c.count > 1 for c in cells), "collapsed cells should aggregate"
    for cell in cells:
        assert cell.example.severity is cell.severity


def test_next_problem_visits_only_problems(matrix):
    assert matrix.problem_count > 0
    seen = set()
    for _ in range(25):
        matrix.action_problem(1)
        assert matrix.cell is not None
        assert matrix.cell.severity in (Severity.WARN, Severity.ERROR)
        seen.add((matrix.cursor_row, matrix.cursor_col))
    assert len(seen) > 1, "n should advance, not sit still"


def test_problem_jump_wraps_and_reverses(matrix):
    matrix.action_problem(1)
    first = (matrix.cursor_row, matrix.cursor_col)
    matrix.action_problem(1)
    matrix.action_problem(-1)
    assert (matrix.cursor_row, matrix.cursor_col) == first


def test_expanding_one_device_does_not_expand_the_rest(matrix):
    matrix.cursor_row = 0
    device = matrix.rows[0].device
    matrix.action_fold_row()
    assert len(matrix.rows) == DEVICES + CHANNELS
    assert sum(1 for r in matrix.rows if r.device == device) == CHANNELS + 1


def test_tx_filter_does_not_empty_the_row_axis(matrix):
    """A bare word narrows rows only — filtering both axes off one word is what
    made the old matrix unusable at scale."""
    matrix.filter_text = "rack04"
    assert 0 < len(matrix.rows) < DEVICES
    assert len(matrix.cols) == DEVICES, "columns must survive a row filter"

    matrix.filter_text = "tx:rack04"
    assert len(matrix.rows) == DEVICES, "rows must survive a column filter"
    assert 0 < len(matrix.cols) < DEVICES

    matrix.filter_text = "rack04 tx:rack09"
    assert 0 < len(matrix.rows) < DEVICES
    assert 0 < len(matrix.cols) < DEVICES


def test_render_cost_is_viewport_bound(big):
    """Rendering must not care how wide the matrix is.

    Has to run inside a real app: `size` comes from layout, so a bare widget
    reports 0x0 and would make this pass without rendering anything.
    """

    async def run() -> float:
        app = CrosspointApp(_Stub(big), iface="eth1")
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.press("2")
            await pilot.pause()
            matrix = app.query_one("#matrix", Matrix)
            assert matrix.size.width > 80, "viewport did not lay out"
            assert matrix._visible_cols()[2] > 10, "no columns visible; test would be vacuous"
            matrix.action_edge(1)  # worst case: scrolled to the far end
            height = matrix.size.height
            start = time.perf_counter()
            for _ in range(10):
                matrix.cursor_row = (matrix.cursor_row + 1) % len(matrix.rows)
                for y in range(height):
                    matrix.render_line(y)
            return (time.perf_counter() - start) / 10

    elapsed = asyncio.run(run())
    assert elapsed < 0.05, f"{elapsed * 1000:.1f} ms per frame"


def test_expand_all_still_renders(big):
    """The pathological case: 12800 rows x 12800 columns, all folds open."""

    async def run() -> tuple[int, int, float]:
        app = CrosspointApp(_Stub(big), iface="eth1")
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.press("2")
            await pilot.press("E")
            await pilot.pause()
            matrix = app.query_one("#matrix", Matrix)
            height = matrix.size.height
            start = time.perf_counter()
            for _ in range(5):
                matrix.cursor_row += 1
                for y in range(height):
                    matrix.render_line(y)
            return len(matrix.rows), len(matrix.cols), (time.perf_counter() - start) / 5

    rows, cols, elapsed = asyncio.run(run())
    assert rows == DEVICES * (CHANNELS + 1)
    assert cols == DEVICES * CHANNELS
    assert elapsed < 0.05, f"{rows}x{cols} took {elapsed * 1000:.1f} ms per frame"
