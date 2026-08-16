"""Entry point for `crosspoint` and `xp`."""

from __future__ import annotations

import argparse

from . import glyphs
from .app import CrosspointApp
from .backends import build


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="crosspoint",
        description="Unofficial read-only TUI for Dante and AES67 audio networks",
    )
    parser.add_argument("--iface", help="network interface Dante lives on")
    parser.add_argument("--backend", default="mock", choices=("mock", "pcap", "live"))
    parser.add_argument("--fixture", help="path to a JSON fixture or capture file")
    parser.add_argument("--ascii", action="store_true", help="plain ASCII glyphs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.ascii:
        glyphs.use_ascii()
    backend = build(args.backend, args.iface, args.fixture)
    CrosspointApp(backend, iface=args.iface).run()


if __name__ == "__main__":
    main()
