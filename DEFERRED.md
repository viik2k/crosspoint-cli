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
- No config file. Do not add one until something actually needs configuring.

## Matrix at scale

- Fold state does not persist across *runs* — that needs a config file, which is not warranted yet.
- Sidebar is a flat 200-row list at production scale; problem counts help, grouping would help more.
- `_rebuild` is ~35 ms at 200 devices and runs on every fold toggle; it is debounced for filter typing but not incremental. Make it incremental only if fold toggles start to feel slow.
- Column axis has no equivalent of the row gutter — see "Decided against" above for why.
