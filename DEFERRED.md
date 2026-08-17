# Deferred

One line each. Outstanding work only; completed items are recorded in the handover.

## HANDOVER — read this first

**Repo is green: `ruff check` clean, 22 tests pass, 8 snapshots current.**

### Done (sessions to date)

- `Channel.bit_depth` / `samples_per_frame`; `Channel.format` is now derived from them
  rather than the invented `PCM24` string. `Channel.label` prefers `friendly_name`.
- `Device.mac_address`, `dante_model_id`, `manufacturer` — surfaced on the Devices tab.
- `Device.aes67_configured` / `aes67_current` + `aes67_pending_reboot`; the Devices tab
  warns when they disagree (fixture: `device-e-io` is configured on, active off).
- Matrix fold state already survived refresh; `Matrix.load` now also re-anchors the
  cursor by **device/channel identity** rather than row index, so a refresh that adds or
  drops a device no longer silently moves you.
- Severity filter: `p` cycles all → problems (warn+error) → errors only. Filters rows
  *and* columns to what still has something wrong, and recomputes `Col.first`.
- `Cell.counts` (per-severity tally) + `Cell.breakdown()`.
- Sidebar: per-device problem count, and selection survives a refresh by device name.
- Detail panel: collapsed cells now show `Cell.breakdown()` rather than only the worst example.
- Matrix status line: shows the active severity filter (`all`, `problems`, or `errors`).
- Clock tab: sortable columns by header click or `s`, with ascending/descending markers.
- Events tab: `RichLog` appends new snapshot events across refreshes without replaying the old sequence.
- Help overlay: generated from the app and matrix `BINDINGS`.
- Command palette: `Ctrl-P` searches device names and moves the matrix cursor to the device.
- `Device.firmware_version`, `software_version`, `min_latency_us`/`max_latency_us`
  (wire units — netaudio's public model converts to seconds, crosspoint does not),
  `tx_flow_count`/`rx_flow_count`, `num_networks`, `is_locked` — parsed and shown on
  the Devices tab (fixture: `device-a-dsp` is locked, `device-f-recorder` reports 3 rx flows).
- Matrix: `m`/`M` jump to the next/previous problem cell **on the current RX device**;
  `n`/`N` remain matrix-wide row-major.

### Done — the "usable by a non-specialist at estate scale" pass

- **Mouse everywhere in the matrix** (`Matrix.on_click`). A cell moves the cursor and
  opens the detail panel; a device name in the gutter folds its row; a TX name in the
  band folds its column. Nothing is mouse-only and nothing is key-only.
- **Legend pinned under the grid** — `◆ connected ● idle ▲ warning ✕ error · no route`,
  built from `glyphs` at call time so `--ascii` is honoured.
- **Devices tab is now a sortable table of every device** (state, problems, clock, sample
  rate, latency, tx/rx) with the old detail block underneath. Sorting is the estate-wide
  question: which device is on the wrong rate, which has the most broken subscriptions.
  Header click sorts and re-click reverses; `s` walks the columns, `S` reverses.
- **Sidebar groups by name prefix** when grouping actually groups
  (`1 < groups <= devices/2`). Headers are disabled `ListItem`s, so `j`/`k` and the mouse
  skip them. Under a header the prefix is stripped off each device name.
- Header carries a one-glance verdict: `✕ 11 bad routes, 1 offline` or `◆ all healthy`.
- Plain English throughout: binding descriptions, `receivers ▾ / tx ▸` instead of
  `RX / TX`, `48 kHz` and `1 ms` instead of `48000 Hz` and `1000 us` (both spellings of
  one number was a bug report waiting to happen), and the crosspoint detail leads with
  the status *sentence* and demotes `CLOCK_DOMAIN 0x001b` to a dim second line.
- Help overlay opens with a short "if you have never used this before" section.
- Selection is shared: picking a device in either list selects it in the other, paints
  the detail block, and parks the routing cursor on it.

### Traps found while doing that — do not undo these

- **Two-way cursor sync between the sidebar and the Devices table is an echo storm.**
  Both hold a cursor over the same devices in different orders (the table is sorted, the
  sidebar is grouped), and moving a cursor makes Textual post the matching
  `Highlighted`. With each list syncing the other it never converged: ~4,400 `_select`
  calls and 19 s to open the routing tab on a *40*-device network. Two things fix it and
  both must stay: `_fill_sidebar`/`_fill_devices` wrap their fill in
  `prevent(ListView.Highlighted)` / `prevent(DataTable.RowHighlighted)` — filling a list
  is not a user picking from it — and only the sidebar direction runs the full sync.
  `tests/test_scale.py::test_filling_the_lists_does_not_storm` is the guard.
- **`DataTable` cells render through rich markup, which does not know Textual's CSS
  variables.** `[$text-dim]` in a cell raises `MarkupError`; use hex from `theme`.
- **Paints are message-driven and messages outrun mounting.** `Matrix.__init__` posts
  `CursorMoved` while the routing pane is still composing, so `query_one("#matrix-status")`
  can raise. Everything now goes through `CrosspointApp._paint`, which no-ops on a
  missing target.
- **`_ready` is already an `App` method** — do not name a helper that.
- The sidebar was a `ListView` inside a `VerticalScroll`; both scrolled, so it had two
  scrollbars. It is a direct child of `#body` now.

### Next, in the order I would do them

1. `PcapBackend`: replay a saved capture through `parse_snapshot` with a netaudio dissector.
2. `LiveBackend`: mDNS discovery plus ARC queries using the optional `netaudio` extra.
3. Live refresh cadence and staleness marking once a real backend exists.
4. Bind real discovery to `--iface`; the flag currently only labels mock output.

### Decided against, do not redo blindly

- **Widening collapsed TX columns so device names fit.** Tried on paper: names need ~14
  cells, which drops the overview from 44 visible devices to 7. The dense 2-cell column
  plus the pinned band name and the status line is the better trade. Revisit only if
  someone finds a way to label without losing the overview.
- **Mutating operations.** v1 is read only by explicit constraint; "I'm liking it" is not
  a reversal of that. They stay in the list below, unimplemented.

## Mutating operations (out of scope for v1 — read only)

The stated purpose of the tool now includes *customising* estate-wide endpoints, which is
this list. Nothing below is implemented, and the read-only constraint still holds until
it is lifted deliberately — see "Decided against" above.

- Add and remove subscriptions, including bulk routing changes.
- Create and delete TX multicast flows.
- Set and reset device names.
- Set and reset TX and RX channel names.
- Set sample rate, encoding bit depth, and latency.
- Enable and disable AES67 mode.
- Configure the device network interface as DHCP or static IP.
- Set AVIO input and output gain.
- Start and stop device volume-control or metering sessions.
- Identify a device or flash its LED.
- Lock and unlock a device.
- Set or clear preferred-leader and clock-source settings.
- Reboot a device.
- Factory-reset or clear a device's configuration.

## Backends

- `PcapBackend`: replay a saved capture through the same `parse_snapshot` path the mock uses. Needs a Dante dissector — import netaudio's rather than writing one; this is its own session.
- `LiveBackend`: zeroconf mDNS browse plus ARC queries, importing `netaudio` (extra: `crosspoint[live]`).
- Live refresh cadence and staleness marking once a real backend exists — `r` is manual-only today.
- Bind discovery to `--iface` for real; the flag is plumbed through but only labels the header under mock.

## Protocol gaps

- Status codes 5, 35, 36, 37, 38, 39, 69, 70, 96, 97, 112, 512, 65536 are named by netaudio but carry no label text; severity is inferred from the constant name and marked TODO in `model.py`. Verified against the 0.2.5 wheel: its label loader runs but returns nothing for these codes. Do not invent text for them.
- Clock detail beyond role/sync/offset/external: netaudio 0.2.5's `DanteDevice` does expose `ptp_v1_role`, `clock_mac` (grandmaster identity) and `preferred_leader`, plus `product_version`, `board_name` and device-level `encoding`/`bit_depth` — none modelled by crosspoint yet.

## UI

- No column-header filter on the Clock tab.
- No config file. Do not add one until something actually needs configuring — fold state,
  sort order and the grouping threshold are the three things that would want one.
- The Devices table drops `address` for width; it is in the detail block underneath.

## Matrix at scale

- Fold state, sort column and sidebar selection do not persist across *runs* — that needs a config file, which is not warranted yet.
- `_rebuild` is ~35 ms at 200 devices and runs on every fold toggle; it is debounced for filter typing but not incremental. Make it incremental only if fold toggles start to feel slow.
- Column axis has no equivalent of the row gutter — see "Decided against" above for why.
- The row gutter is 20 cells, so a device name truncates at 19. Court naming (`CR03-Ceiling-Mic-Array`) will hit this. Widening it costs matrix columns one-for-one; the status line carries the full name in the meantime.
- No prefix-level fold in the matrix itself. The sidebar groups by room; the grid does not, so 200 devices are still 200 rows. A third fold level (room ▸ device ▸ channel) is the real answer and is a session of its own.
- `s` / `S` appear in the footer on tabs that have no sortable table, where they do nothing.
