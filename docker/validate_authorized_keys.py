#!/usr/bin/env python3
"""Validate RunPod key environment variables without exposing their contents."""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


ALLOWED_TYPES = {
    "ssh-ed25519",
    "sk-ssh-ed25519@openssh.com",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ecdsa-sha2-nistp256@openssh.com",
}
MAX_KEYS = 256
MAX_ENV_BYTES = 1024 * 1024


class InvalidKeySet(ValueError):
    pass


def _blob_type(blob: bytes) -> str:
    if len(blob) < 4:
        raise InvalidKeySet("truncated key blob")
    length = struct.unpack(">I", blob[:4])[0]
    if length <= 0 or length > len(blob) - 4:
        raise InvalidKeySet("invalid key type length")
    try:
        return blob[4 : 4 + length].decode("ascii")
    except UnicodeDecodeError as exc:
        raise InvalidKeySet("non-ASCII key type") from exc


def _validate_line(line: str) -> tuple[str, str]:
    # Deliberately reject authorized_keys options. A RunPod variable supplies key
    # material, not per-key commands or policy overrides.
    parts = line.split(None, 2)
    if len(parts) < 2 or parts[0] not in ALLOWED_TYPES:
        raise InvalidKeySet("unsupported or malformed public key")
    key_type, encoded = parts[0], parts[1]
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InvalidKeySet("invalid public key encoding") from exc
    if _blob_type(blob) != key_type:
        raise InvalidKeySet("key type does not match key blob")

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as probe:
        probe.write(f"{key_type} {encoded}\n")
        probe.flush()
        result = subprocess.run(
            ["ssh-keygen", "-l", "-E", "sha256", "-f", probe.name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    if result.returncode != 0:
        raise InvalidKeySet("ssh-keygen rejected public key")

    canonical = f"{key_type} {encoded}"
    if len(parts) == 3 and parts[2].strip():
        canonical = f"{canonical} {parts[2].strip()}"
    return f"{key_type} {encoded}", canonical


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("usage: validate_authorized_keys.py OUTPUT [SOURCE]", file=sys.stderr)
        return 64

    # Match RunPod/Miru source precedence. SSH_PUBLIC_KEY is the documented
    # per-Pod override, not an additional key set.
    supplied = os.environ.get("SSH_PUBLIC_KEY", "")
    if not supplied and len(sys.argv) == 3:
        source = Path(sys.argv[2])
        try:
            supplied = source.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            print("SSH disabled: root authorized_keys source is unreadable", file=sys.stderr)
            return 2
    if not supplied:
        supplied = os.environ.get("PUBLIC_KEY", "")

    if len(supplied.encode("utf-8")) > MAX_ENV_BYTES:
        print("SSH disabled: supplied key set is too large", file=sys.stderr)
        return 2

    raw_lines = [
        line.strip()
        for line in supplied.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    lines = [line for line in raw_lines if line]
    if not lines:
        return 3
    if len(lines) > MAX_KEYS:
        print("SSH disabled: supplied key set contains too many keys", file=sys.stderr)
        return 2

    try:
        unique: dict[str, str] = {}
        for line in lines:
            identity, canonical = _validate_line(line)
            unique.setdefault(identity, canonical)
    except InvalidKeySet:
        print("SSH disabled: supplied key set contains an invalid entry", file=sys.stderr)
        return 2

    destination = Path(sys.argv[1])
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.parent.chmod(0o700)
    fd, temporary_name = tempfile.mkstemp(prefix="authorized_keys.", dir=destination.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as handle:
            for canonical in unique.values():
                handle.write(canonical)
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        destination.chmod(0o600)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
