from __future__ import annotations

import contextlib
import http.server
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from typing import Iterator


ROOT = Path(__file__).resolve().parents[2]
HEALTHCHECK = ROOT / "docker" / "healthcheck.py"


class HealthHandler(http.server.BaseHTTPRequestHandler):
    status = 200
    requests: list[tuple[str, str]] = []

    def do_GET(self) -> None:
        type(self).requests.append((self.path, self.headers.get("Host", "")))
        body = b'{"status":"ok"}'
        self.send_response(type(self).status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class IPv6HTTPServer(http.server.HTTPServer):
    address_family = socket.AF_INET6


@contextlib.contextmanager
def health_server(
    host: str,
    *,
    status: int = 200,
) -> Iterator[tuple[http.server.HTTPServer, type[HealthHandler]]]:
    handler = type(
        "CaseHealthHandler",
        (HealthHandler,),
        {"requests": [], "status": status},
    )
    server_class = IPv6HTTPServer if ":" in host else http.server.HTTPServer
    server = server_class((host, 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, handler
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def run_healthcheck(
    bind_host: str,
    port: int,
    *,
    runtime_env: Path | None = None,
    ssh_port: int | None = None,
    sshd_pid_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SEE_THROUGH_BIND_HOST"] = bind_host
    env["SEE_THROUGH_PORT"] = str(port)
    if runtime_env is not None:
        env["SEE_THROUGH_RUNTIME_ENV_FILE"] = str(runtime_env)
    if ssh_port is not None:
        env["SEE_THROUGH_SSH_HEALTHCHECK_PORT"] = str(ssh_port)
    if sshd_pid_file is not None:
        env["SEE_THROUGH_SSHD_PID_FILE"] = str(sshd_pid_file)
    return subprocess.run(
        [sys.executable, str(HEALTHCHECK)],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=5,
    )


def assert_success(
    listen_host: str,
    bind_host: str,
    expected_authority_host: str,
) -> None:
    with health_server(listen_host) as (server, handler):
        port = server.server_address[1]
        result = run_healthcheck(bind_host, port)
        assert result.returncode == 0, result.stderr
        assert handler.requests == [
            ("/healthz", f"{expected_authority_host}:{port}")
        ]


def main() -> None:
    assert_success("127.0.0.1", "127.0.0.1", "127.0.0.1")
    assert_success("127.0.0.1", "0.0.0.0", "127.0.0.1")

    with health_server("127.0.0.1", status=503) as (server, handler):
        result = run_healthcheck("127.0.0.1", server.server_address[1])
        assert result.returncode != 0
        assert "/healthz returned HTTP 503" in result.stderr
        assert handler.requests[0][0] == "/healthz"

    with tempfile.TemporaryDirectory() as temporary:
        runtime_env = Path(temporary) / "runtime.env"
        runtime_env.write_text(
            "export SEE_THROUGH_SSH_REQUIRED=1\n",
            encoding="utf-8",
        )
        with health_server("127.0.0.1") as (server, _):
            result = run_healthcheck(
                "127.0.0.1",
                server.server_address[1],
                runtime_env=runtime_env,
                ssh_port=1,
                sshd_pid_file=Path(temporary) / "sshd.pid",
            )
        assert result.returncode != 0
        assert "SSH was enabled at startup but its PID file is invalid" in result.stderr

    if socket.has_ipv6:
        try:
            assert_success("::1", "::", "[::1]")
        except OSError:
            pass

    print("container healthcheck contract: OK")


if __name__ == "__main__":
    main()
