# crosspoint

A read-only terminal UI for inspecting Dante and AES67 audio networks over SSH.

Unofficial and read-only. Not affiliated with, endorsed by, or supported by Audinate.
Dante is a trademark of its respective owner; this project is not a Dante product and
ships no vendor branding.

A crosspoint is one cell in a routing matrix. The matrix is the centrepiece of this tool.

## What it is for

Dante Controller is a desktop GUI. When the rack is at the far end of a building and all
you have is a shell, there is no equivalent. crosspoint shows device state, the
subscription matrix and clock health in a terminal, and never writes to a device.

## Status

Early. This session's build runs against JSON fixtures only.

| backend | what it does | state |
| --- | --- | --- |
| `mock` | JSON fixtures, deterministic, zero network | working |
| `pcap` | replay a saved capture | not built |
| `live` | real mDNS discovery plus ARC queries | not built |

## Install

```sh
uv sync
uv run crosspoint --backend mock
```

Two console scripts, same entry point: `crosspoint` and `xp`.

```
--iface IFACE      network interface Dante lives on
--backend {mock,pcap,live}
--fixture PATH     JSON fixture or capture file
--ascii            plain ASCII glyphs instead of Nerd Font
```

The default glyph set needs a Nerd Font. `--ascii` swaps the whole set for plain
consoles and serial terminals.

## Driving it

Nothing here is keyboard-only. The mouse does what it does in a desktop GUI:

- Click a **cell** in the routing grid to see what is wired to what.
- Click a **device name** down the left of the grid to open or close its channels;
  click a **transmitter name** along the top to open or close its columns.
- Click a device in the list or the Devices table to select it.
- Click a **column header** to sort by it, click again to reverse.
- The wheel scrolls.

A legend sits under the grid, and `?` opens a key reference generated from the
bindings themselves, so it cannot drift out of date.

```
j k h l ← → ↑ ↓   move a cell        1 2 3 4  devices / routing / clock / events
g G               top / bottom       Tab      cycle focus
PgUp PgDn ^b ^f   page rows          \        toggle the device list
H L               page columns       r        reload
0 $ Home End      first / last col   ?        help
n N               next / prev problem cell    q  quit
m M               next / prev problem on this device
p                 cycle all / problems / errors
s S               sort by next column / reverse it  (Devices and Clock tabs)
Ctrl-P            jump to a device by name
[ ]               prev / next TX device block
Space             open / close this receiver (row)
t                 open / close this transmitter (column)
E C               open all / close all
Enter             inspect the crosspoint under the cursor
/                 filter, Esc clears
```

## The estate at a glance

The header carries the verdict — `11 bad routes, 1 offline`, or `all healthy` —
before anything else on the screen.

The **Devices** tab is one row per device: state, problem count, clock role,
sample rate, latency, channel counts. Sort by any column, because "which device
is on the wrong sample rate" and "which device has the most broken
subscriptions" are questions about the whole estate, not about one device. The
device under the cursor is expanded underneath.

The device list down the left side groups by name prefix once grouping actually
groups — `CR03-Ceiling-Mic` files under `CR03` — so 200 devices read as the dozen
rooms they live in, each with its own offline and fault tally. Selecting a device
anywhere selects it everywhere, and parks the routing grid on it.

## The routing matrix at scale

A 200-device network is a 12,800 x 12,800 grid, and it is 99.99% empty — an RX
channel subscribes to at most one TX channel, so there are at most 12,800 filled
cells in 164 million. Rendering that is not the problem (only the visible window is
ever drawn, ~3 ms a frame either way). Crossing it is: 12,800 keypresses per axis.

So device blocks start **collapsed**. The default view is one row and one column per
device, and each cell shows the worst status of every subscription underneath it —
which device pairs are unhealthy, on one screen. Open a block with `Space` (rows) or
`t` (columns) when you want its channels.

The grid is glyphs, so a legend is pinned under it: `◆ connected`, `● idle`,
`▲ warning`, `✕ error`, `· no route`. ERROR is a distinct shape as well as a
distinct colour, so the display never depends on colour alone.

Navigation is built around jumping rather than scrolling:

- `n` / `N` — next / previous problem cell. This is the main way to work a large rack.
- `[` / `]` — previous / next TX device block.
- `H` / `L`, `0` / `$` — page and jump along the column axis.
- `/` — filter. **Bare words narrow rows; `tx:` narrows columns.** So `rack04 tx:dsp`
  shows rack04's inputs against the dsp's outputs. Filtering both axes off one word
  would empty the column axis the moment you typed an RX device name.

A status line under the matrix always shows the RX and TX device and channel at the
cursor, your position on both axes, and the total problem count — at this size you
should never have to guess where you are.

## Read only

v1 does not change subscriptions, rename anything, or write configuration. It is meant
to be safe to point at a production rack. Anything that would mutate a device is listed
in [DEFERRED.md](DEFERRED.md) and is not implemented.

## Protocol source

Protocol knowledge comes from
[network-audio-controller](https://github.com/chris-ritsen/network-audio-controller)
(`netaudio`, Unlicense) — its packet dissector and data model are the reference. Nothing
about the wire format is guessed here; fields netaudio does not cover are marked `TODO`
in the source and left alone.

Subscription status codes are mirrored from netaudio's `SUBSCRIPTION_STATUS_*` constants
and each is mapped to one of OK / ACTIVE / WARN / ERROR. Note that `netaudio` 0.2.5 builds
its status table at import time from a labels file the published wheel does not ship, so
the codes in `crosspoint/model.py` are frozen from the last revision that hardcoded them
(tag `v0.0.12`) rather than imported.

The live backend, when built, will import `netaudio` rather than shell out to its CLI.
It is an optional extra (`crosspoint[live]`) because the wheel is platform-specific and
carries a native library plus a large dependency tree that mock and pcap do not need.

## Theme

"pitwall" — red on black, brutalist. Square borders, no gradients, strict column
alignment. Status colours are a separate scale from the theme chrome: OK is deliberately
quiet so that healthy cells recede, and ERROR is always paired with a distinct glyph so
the display never depends on colour alone.

## Tests

```sh
uv run pytest
uv run pytest --snapshot-update   # after an intentional visual change
```

Snapshot tests cover one render per tab against the mock fixture. The fixture contains a
broken subscription, a clock slave with a large offset, an offline device and a
64-channel device — the states worth rendering well.

`tests/test_scale.py` builds a 200-device network and asserts the properties that keep
it usable: the default view is one row and column per device, `_rebuild` cost tracks the
subscription count rather than devices squared, `n` visits only problem cells, a row
filter does not empty the column axis, and a frame still renders in well under 50 ms with
every fold open (13,000 x 12,800).
