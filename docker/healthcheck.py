#!/usr/bin/env python3
from __future__ import annotations

import http.client
import ipaddress
import os
from pathlib import Path
import socket
import subprocess


TIMEOUT_SECONDS = 3.0
RUNTIME_ENV = Path(
    os.environ.get(
        "SEE_THROUGH_RUNTIME_ENV_FILE",
        "/run/see-through/runtime.env",
    )
)
SSHD_PID_FILE = Path(
    os.environ.get(
        "SEE_THROUGH_SSHD_PID_FILE",
        "/run/see-through/sshd.pid",
    )
)


def ssh_required() -> bool:
    try:
        lines = RUNTIME_ENV.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    return "export SEE_THROUGH_SSH_REQUIRED=1" in lines


def check_ssh() -> None:
    try:
        pid_text = SSHD_PID_FILE.read_text(encoding="ascii").strip()
        pid = int(pid_text, 10)
        if pid <= 0:
            raise ValueError
    except (FileNotFoundError, ValueError):
        raise SystemExit("SSH was enabled at startup but its PID file is invalid")

    try:
        os.kill(pid, 0)
    except OSError as exc:
        raise SystemExit(
            f"SSH was enabled at startup but listener PID {pid} is not alive: {exc}"
        ) from exc

    result = subprocess.run(
        ["/usr/sbin/sshd", "-t"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or f"status {result.returncode}"
        raise SystemExit(f"sshd configuration validation failed: {message}")

    ssh_port = int(os.environ.get("SEE_THROUGH_SSH_HEALTHCHECK_PORT", "22"), 10)
    try:
        with socket.create_connection(
            ("127.0.0.1", ssh_port),
            timeout=TIMEOUT_SECONDS,
        ):
            pass
    except OSError as exc:
        raise SystemExit(
            f"SSH was enabled at startup but 127.0.0.1:{ssh_port} "
            f"is not accepting connections: {exc}"
        ) from exc


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

    if ssh_required():
        check_ssh()


if __name__ == "__main__":
    main()
