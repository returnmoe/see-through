#!/usr/bin/env python3
from __future__ import annotations

import http.client
import ipaddress
import os


TIMEOUT_SECONDS = 3.0


def main() -> None:
    host = ipaddress.ip_address(
        os.environ.get("SEE_THROUGH_BIND_HOST", "127.0.0.1").strip()
    )
    if host.is_unspecified:
        host = ipaddress.ip_address("127.0.0.1" if host.version == 4 else "::1")

    port = int(os.environ.get("SEE_THROUGH_PORT", "4321"), 10)
    if not 1 <= port <= 65535:
        raise SystemExit("SEE_THROUGH_PORT must be between 1 and 65535")

    authority_host = f"[{host}]" if host.version == 6 else str(host)
    connection = http.client.HTTPConnection(
        str(host),
        port,
        timeout=TIMEOUT_SECONDS,
    )
    try:
        connection.request(
            "GET",
            "/healthz",
            headers={
                "Accept": "application/json",
                "Connection": "close",
                "Host": f"{authority_host}:{port}",
            },
        )
        response = connection.getresponse()
        response.read()
        if response.status != 200:
            raise SystemExit(f"/healthz returned HTTP {response.status}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
