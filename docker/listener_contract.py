#!/usr/bin/env python3
"""Validate the web listener and emit its SSH PermitOpen destination."""

from __future__ import annotations

import ipaddress
import os
import sys


def main() -> int:
    host = os.environ.get("SEE_THROUGH_BIND_HOST", "127.0.0.1").strip()
    port_text = os.environ.get("SEE_THROUGH_PORT", "4321").strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        print("SEE_THROUGH_BIND_HOST must be an IPv4 or IPv6 address literal", file=sys.stderr)
        return 64
    try:
        port = int(port_text, 10)
    except ValueError:
        print("SEE_THROUGH_PORT must be an integer", file=sys.stderr)
        return 64
    if port < 1024 or port > 65535 or port == 22:
        print("SEE_THROUGH_PORT must be between 1024 and 65535 and cannot be 22", file=sys.stderr)
        return 64

    if address.is_unspecified:
        destination = ipaddress.ip_address("127.0.0.1" if address.version == 4 else "::1")
    else:
        destination = address
    permit_open = f"[{destination}]:{port}" if destination.version == 6 else f"{destination}:{port}"
    print(permit_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
