from __future__ import annotations

import argparse
import asyncio
import json

import uvicorn

from .config import Settings
from .model_cache import GPUProbe, ModelManager


def main() -> None:
    parser = argparse.ArgumentParser(prog="see-through-server")
    subcommands = parser.add_subparsers(dest="command")
    prefetch = subcommands.add_parser("prefetch", help="download a pinned model bundle")
    prefetch.add_argument("selection", choices=["auto", "bf16", "nf4"], nargs="?", default="auto")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "prefetch":
        asyncio.run(_prefetch(settings, args.selection))
        return
    uvicorn.run(
        "seethrough_server.app:app",
        host=settings.bind_host,
        port=settings.port,
        proxy_headers=False,
        server_header=False,
        access_log=True,
    )


async def _prefetch(settings: Settings, selection: str) -> None:
    manager = ModelManager(settings)
    bundle = selection
    if selection == "auto":
        gpu = await asyncio.to_thread(GPUProbe().inspect)
        memory = int(gpu["memory_mib"])
        if memory < 8 * 1024:
            raise SystemExit("No compatible NVIDIA GPU detected (8 GiB minimum)")
        bundle = "bf16" if memory >= 11 * 1024 else "nf4"
    paths = await manager.ensure_bundle(bundle)
    print(json.dumps({"bundle": bundle, "paths": paths}, sort_keys=True))


if __name__ == "__main__":
    main()
