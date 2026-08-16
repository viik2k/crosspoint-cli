"""Discovery backends. The UI never imports one of these — it is handed an
instance at startup.

`snapshot` is async because the live backend will do network I/O on it; the mock
just returns. Sync-now-async-later is a refactor, so pay the `async` up front.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .model import Channel, ClockStatus, Device, Snapshot, Subscription, status

FIXTURE = Path(__file__).parent / "fixtures" / "mock.json"


@runtime_checkable
class Backend(Protocol):
    name: str

    async def snapshot(self) -> Snapshot: ...


def _channels(raw: list[dict[str, Any]], direction: str) -> tuple[Channel, ...]:
    return tuple(
        Channel(
            index=c["index"],
            name=c["name"],
            direction=direction,
            bit_depth=c.get("bit_depth"),
            samples_per_frame=c.get("samples_per_frame"),
            friendly_name=c.get("friendly_name"),
        )
        for c in raw
    )


def parse_snapshot(data: dict[str, Any]) -> Snapshot:
    """dict -> Snapshot. Shared by mock and (later) pcap replay."""
    devices = []
    for d in data.get("devices", []):
        clock = ClockStatus(**d["clock"]) if d.get("clock") else None
        devices.append(
            Device(
                name=d["name"],
                id=d["id"],
                addresses=tuple(d.get("addresses", ())),
                model=d.get("model"),
                sample_rate=d.get("sample_rate"),
                latency_us=d.get("latency_us"),
                tx_channels=_channels(d.get("tx_channels", []), "tx"),
                rx_channels=_channels(d.get("rx_channels", []), "rx"),
                clock=clock,
                online=d.get("online", True),
                last_seen=d.get("last_seen"),
                mac_address=d.get("mac_address"),
                dante_model_id=d.get("dante_model_id"),
                manufacturer=d.get("manufacturer"),
                aes67_configured=d.get("aes67_configured"),
                aes67_current=d.get("aes67_current"),
                firmware_version=d.get("firmware_version"),
                software_version=d.get("software_version"),
                min_latency_us=d.get("min_latency_us"),
                max_latency_us=d.get("max_latency_us"),
                tx_flow_count=d.get("tx_flow_count"),
                rx_flow_count=d.get("rx_flow_count"),
                num_networks=d.get("num_networks"),
                is_locked=d.get("is_locked"),
            )
        )

    subscriptions = tuple(
        Subscription(
            rx_device=s["rx_device"],
            rx_channel=s["rx_channel"],
            tx_device=s["tx_device"],
            tx_channel=s["tx_channel"],
            status=status(s["status"]),
            reason=s.get("reason"),
        )
        for s in data.get("subscriptions", [])
    )

    return Snapshot(
        devices=tuple(devices),
        subscriptions=subscriptions,
        events=tuple(data.get("events", ())),
        taken_at=data.get("taken_at", 0.0),
    )


class MockBackend:
    """JSON fixtures. Deterministic, zero network."""

    name: str = "mock"

    def __init__(self, path: Path | str | None = None, iface: str | None = None) -> None:
        self.path: Path = Path(path) if path else FIXTURE
        self.iface: str | None = iface
        self._data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))

    async def snapshot(self) -> Snapshot:
        return parse_snapshot(self._data)


class PcapBackend:
    """Replay a saved capture. Not built yet — see DEFERRED.md."""

    name: str = "pcap"

    def __init__(self, path: Path | str, iface: str | None = None):
        raise NotImplementedError("pcap replay is not built yet")

    async def snapshot(self) -> Snapshot:
        raise NotImplementedError("pcap replay is not built yet")


def build(kind: str, iface: str | None = None, fixture: str | None = None) -> Backend:
    if kind == "mock":
        return MockBackend(fixture, iface)
    if kind == "pcap":
        if not fixture:
            raise SystemExit("--fixture PATH is required for the pcap backend")
        return PcapBackend(fixture, iface)
    if kind == "live":
        raise SystemExit("the live backend is not built yet; use --backend mock")
    raise SystemExit(f"unknown backend: {kind}")


def demo() -> None:
    import asyncio

    snap = asyncio.run(MockBackend().snapshot())
    assert len(snap.devices) >= 4, snap.devices
    assert any(not d.online for d in snap.devices), "fixture needs an offline device"
    assert any(len(d.rx_channels) == 64 for d in snap.devices), "fixture needs a 64ch device"
    assert any(s.severity.value == "error" for s in snap.subscriptions), "fixture needs a break"
    assert snap.clock_master, "fixture needs a clock master"
    drifting = False
    for device in snap.devices:
        if device.clock and device.clock.role == "slave" and abs(device.clock.frequency_offset_ppm or 0) > 5:
            drifting = True
            break
    assert drifting, "need a drifting slave"
    print(f"ok: {len(snap.devices)} devices, {len(snap.subscriptions)} subscriptions")


if __name__ == "__main__":
    demo()
