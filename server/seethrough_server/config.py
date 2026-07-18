from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

VALID_PREFETCH = frozenset({"auto", "off", "bf16", "nf4"})


def _parse_host(value: str) -> str:
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ValueError(
            "SEE_THROUGH_BIND_HOST must be an IPv4 or IPv6 address literal"
        ) from exc


def _parse_port(value: str) -> int:
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise ValueError("SEE_THROUGH_PORT must be an integer") from exc
    if not 1024 <= port <= 65535 or port == 22:
        raise ValueError("SEE_THROUGH_PORT must be between 1024 and 65535")
    return port


@dataclass(frozen=True, slots=True)
class Settings:
    bind_host: str
    port: int
    data_dir: Path
    model_cache: Path
    prefetch: str
    repo_root: Path
    web_dist: Path
    max_upload_bytes: int = 50 * 1024 * 1024
    max_image_dimension: int = 10240
    model_reserve_bytes: int = 5 * 1024**3

    @property
    def bind_address(self) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        return ipaddress.ip_address(self.bind_host)

    @property
    def loopback_only(self) -> bool:
        return self.bind_address.is_loopback

    @property
    def health_host(self) -> str:
        address = self.bind_address
        if address.is_unspecified:
            return "::1" if address.version == 6 else "127.0.0.1"
        return str(address)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        repo_root: Path | None = None,
        web_dist: Path | None = None,
    ) -> "Settings":
        values = os.environ if env is None else env
        root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
        host = _parse_host(values.get("SEE_THROUGH_BIND_HOST", "127.0.0.1"))
        port = _parse_port(values.get("SEE_THROUGH_PORT", "4321"))

        data_dir = Path(
            values.get("SEE_THROUGH_DATA_DIR", "/home/seethrough/data")
        ).expanduser()
        model_cache_value = values.get("SEE_THROUGH_MODEL_CACHE")
        if model_cache_value is None:
            model_cache_value = values.get(
                "HF_HOME", "/home/seethrough/.cache/huggingface"
            )
        model_cache = Path(model_cache_value).expanduser()

        prefetch = values.get("SEE_THROUGH_PREFETCH", "auto").strip().lower()
        if prefetch not in VALID_PREFETCH:
            choices = ", ".join(sorted(VALID_PREFETCH))
            raise ValueError(f"SEE_THROUGH_PREFETCH must be one of: {choices}")

        return cls(
            bind_host=host,
            port=port,
            data_dir=data_dir.resolve(),
            model_cache=model_cache.resolve(),
            prefetch=prefetch,
            repo_root=root,
            web_dist=(web_dist or root / "web" / "dist").resolve(),
        )
