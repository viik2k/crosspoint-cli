"""Data model. Plain dataclasses, no ORM.

Subscription status codes mirror netaudio's `SUBSCRIPTION_STATUS_*` constants
(github.com/chris-ritsen/network-audio-controller, const.py @ v0.0.12 — the last
revision where the table was hardcoded rather than loaded from a labels file
that the published wheel does not ship).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    """How loudly a subscription should shout. Drives colour and glyph."""

    OK = "ok"
    ACTIVE = "active"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Status:
    code: int
    name: str
    severity: Severity
    label: str | None = None  # None == netaudio names the code but ships no text


def _s(code: int, name: str, severity: Severity, label: str | None = None) -> Status:
    return Status(code, name, severity, label)


# Mirrored from netaudio const.py. Order follows the numeric code.
STATUSES: dict[int, Status] = {
    s.code: s
    for s in (
        _s(0, "NONE", Severity.OK, "No subscription for this channel"),
        _s(1, "UNRESOLVED", Severity.ERROR, "Unresolved: channel not present on the network"),
        _s(2, "RESOLVED", Severity.WARN, "Resolved: channel found, preparing to create flow"),
        _s(3, "RESOLVE_FAIL", Severity.ERROR, "Can't resolve subscription"),
        _s(4, "SUBSCRIBE_SELF", Severity.ACTIVE, "Connected (self)"),
        _s(5, "RESOLVED_NONE", Severity.ERROR),  # TODO: netaudio ships no label text
        _s(7, "IDLE", Severity.WARN, "Flow creation idle: insufficient information to create flow"),
        _s(8, "IN_PROGRESS", Severity.WARN, "Flow creation in progress"),
        _s(9, "DYNAMIC", Severity.ACTIVE, "Connected (unicast)"),
        _s(10, "STATIC", Severity.ACTIVE, "Connected (multicast)"),
        _s(14, "MANUAL", Severity.ACTIVE, "Manually configured"),
        _s(15, "NO_CONNECTION", Severity.ERROR, "No connection: could not communicate with transmitter"),
        _s(16, "CHANNEL_FORMAT", Severity.ERROR, "Incorrect channel format: source and destination do not match"),
        _s(17, "BUNDLE_FORMAT", Severity.ERROR, "Incorrect flow format: incompatible with receiver"),
        _s(18, "NO_RX", Severity.ERROR, "No more flows (RX): receiver cannot support any more flows"),
        _s(19, "RX_FAIL", Severity.ERROR, "Receiver setup failed: unexpected error on receiver"),
        _s(20, "NO_TX", Severity.ERROR, "No more flows (TX): transmitter cannot support any more flows"),
        _s(21, "TX_FAIL", Severity.ERROR, "Transmitter setup failed: unexpected error on transmitter"),
        _s(22, "QOS_FAIL_RX", Severity.ERROR, "Receive bandwidth exceeded"),
        _s(23, "QOS_FAIL_TX", Severity.ERROR, "Transmit bandwidth exceeded"),
        _s(24, "TX_REJECTED_ADDR", Severity.ERROR, "Transmitter rejected address"),
        _s(25, "INVALID_MSG", Severity.ERROR, "Transmitter rejected message"),
        _s(26, "CHANNEL_LATENCY", Severity.ERROR, "Source demands more latency than the receiver has available"),
        _s(27, "CLOCK_DOMAIN", Severity.ERROR, "Mismatched clock domains"),
        _s(28, "UNSUPPORTED", Severity.ERROR, "Unsupported feature"),
        _s(29, "RX_LINK_DOWN", Severity.ERROR, "RX link down"),
        _s(30, "TX_LINK_DOWN", Severity.ERROR, "TX link down"),
        _s(31, "DYNAMIC_PROTOCOL", Severity.ACTIVE, "Dynamic protocol"),
        _s(32, "INVALID_CHANNEL", Severity.ERROR, "Invalid channel"),
        _s(33, "TX_SCHEDULER_FAILURE", Severity.ERROR, "TX scheduler failure"),
        _s(34, "SUBSCRIBE_SELF_POLICY", Severity.ERROR, "Subscription to own signal disallowed by device"),
        _s(35, "TX_NOT_READY", Severity.WARN),  # TODO: netaudio ships no label text
        _s(36, "RX_NOT_READY", Severity.WARN),  # TODO: netaudio ships no label text
        _s(37, "TX_FANOUT_LIMIT_REACHED", Severity.ERROR),  # TODO: no label text
        _s(38, "TX_CHANNEL_ENCRYPTED", Severity.ERROR),  # TODO: no label text
        _s(39, "TX_RESPONSE_UNEXPECTED", Severity.ERROR),  # TODO: no label text
        _s(64, "TEMPLATE_MISMATCH_DEVICE", Severity.ERROR, "Template mismatch (device)"),
        _s(65, "TEMPLATE_MISMATCH_FORMAT", Severity.ERROR, "Template mismatch (format)"),
        _s(66, "TEMPLATE_MISSING_CHANNEL", Severity.ERROR, "Template missing channel"),
        _s(67, "TEMPLATE_MISMATCH_CONFIG", Severity.ERROR, "Template mismatch (config)"),
        _s(68, "TEMPLATE_FULL", Severity.ERROR, "Template full"),
        _s(69, "RX_UNSUPPORTED_SUB_MODE", Severity.ERROR),  # TODO: no label text
        _s(70, "TX_UNSUPPORTED_SUB_MODE", Severity.ERROR),  # TODO: no label text
        _s(96, "TX_ACCESS_CONTROL_DENIED", Severity.ERROR),  # TODO: no label text
        _s(97, "TX_ACCESS_CONTROL_PENDING", Severity.WARN),  # TODO: no label text
        _s(112, "HDCP_NEGOTIATION_ERROR", Severity.ERROR),  # TODO: no label text
        _s(255, "SYSTEM_FAIL", Severity.ERROR, "System failure"),
        _s(256, "FLAG_NO_ADVERT", Severity.WARN, "No audio data"),
        _s(512, "FLAG_NO_DBCP", Severity.WARN),  # TODO: netaudio ships no label text
        _s(65536, "NO_DATA", Severity.WARN),  # TODO: netaudio ships no label text
    )
}


def status(code: int) -> Status:
    """Look up a status code. Unknown codes are ERROR — an unreadable rack is not fine."""
    return STATUSES.get(code) or Status(code, f"UNKNOWN_{code}", Severity.ERROR)


@dataclass(frozen=True, slots=True)
class Channel:
    index: int
    name: str
    direction: str  # "tx" | "rx"
    # netaudio carries bit depth and samples-per-frame separately; there is no
    # "format" string on the wire, so it is derived rather than stored.
    bit_depth: int | None = None
    samples_per_frame: int | None = None
    friendly_name: str | None = None

    @property
    def format(self) -> str | None:
        if self.bit_depth is None:
            return None
        text = f"PCM{self.bit_depth}"
        return text if self.samples_per_frame is None else f"{text}/{self.samples_per_frame}"

    @property
    def label(self) -> str:
        return self.friendly_name or self.name


@dataclass(frozen=True, slots=True)
class ClockStatus:
    role: str  # "master" | "slave" | "unknown"
    sync_state: str  # "locked" | "unlocked" | "unknown"
    source: str | None = None
    frequency_offset_ppm: float | None = None
    external_sync: bool = False


@dataclass(frozen=True, slots=True)
class Device:
    name: str
    id: str  # mDNS-derived identifier; see mac_address / dante_model_id below
    addresses: tuple[str, ...] = ()
    model: str | None = None
    sample_rate: int | None = None
    latency_us: int | None = None
    tx_channels: tuple[Channel, ...] = ()
    rx_channels: tuple[Channel, ...] = ()
    clock: ClockStatus | None = None
    online: bool = True
    last_seen: float | None = None
    # Identity beyond the mDNS name, all straight off netaudio's device model.
    mac_address: str | None = None
    dante_model_id: str | None = None
    manufacturer: str | None = None
    # AES67 has a configured setting and a currently-active state; they differ
    # until the device is rebooted, which is exactly the case worth showing.
    aes67_configured: bool | None = None
    aes67_current: bool | None = None
    # Remaining identity/capability fields off netaudio's device model. Latency
    # bounds are microseconds like `latency_us` — the wire reports
    # min_latency_us/max_latency_us and netaudio converts to seconds; crosspoint
    # keeps the wire units.
    firmware_version: str | None = None
    software_version: str | None = None
    min_latency_us: int | None = None
    max_latency_us: int | None = None
    tx_flow_count: int | None = None
    rx_flow_count: int | None = None
    num_networks: int | None = None
    is_locked: bool | None = None

    @property
    def aes67_pending_reboot(self) -> bool:
        return (
            self.aes67_configured is not None
            and self.aes67_current is not None
            and self.aes67_configured != self.aes67_current
        )


@dataclass(frozen=True, slots=True)
class Subscription:
    rx_device: str
    rx_channel: str
    tx_device: str
    tx_channel: str
    status: Status
    reason: str | None = None

    @property
    def severity(self) -> Severity:
        return self.status.severity


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One complete read of the network. What a backend hands the UI."""

    devices: tuple[Device, ...] = ()
    subscriptions: tuple[Subscription, ...] = ()
    events: tuple[str, ...] = field(default=())
    taken_at: float = 0.0

    @property
    def clock_master(self) -> str | None:
        for device in self.devices:
            if device.clock and device.clock.role == "master":
                return device.name
        return None
