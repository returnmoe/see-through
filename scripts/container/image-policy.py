#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


DOCKER_GZIP_MEDIA = "application/vnd.docker.image.rootfs.diff.tar.gzip"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--select-amd64", action="store_true")
    parser.add_argument("--image-config", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-layer", type=int, default=1342177280)
    parser.add_argument("--max-total", type=int, default=6442450944)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    if args.select_amd64:
        matches = [
            item for item in manifest.get("manifests", [])
            if item.get("platform", {}).get("os") == "linux"
            and item.get("platform", {}).get("architecture") == "amd64"
            and item.get("platform", {}).get("os") != "unknown"
        ]
        if len(matches) != 1:
            raise SystemExit(f"expected exactly one linux/amd64 manifest, found {len(matches)}")
        print(matches[0]["digest"])
        return 0

    media = manifest.get("mediaType")
    if media != "application/vnd.docker.distribution.manifest.v2+json":
        raise SystemExit(f"deployment reference must be a Docker schema-2 manifest: {media}")
    layers = manifest.get("layers", [])
    if not layers:
        raise SystemExit("image manifest contains no layers")
    bad_media = sorted({item.get("mediaType") for item in layers} - {DOCKER_GZIP_MEDIA})
    if bad_media:
        raise SystemExit(f"RunPod deployment layers must use gzip media types: {bad_media}")
    largest = max(item["size"] for item in layers)
    total = sum(item["size"] for item in layers)
    if largest > args.max_layer:
        raise SystemExit(f"largest compressed layer is {largest} bytes (limit {args.max_layer})")
    if total > args.max_total:
        raise SystemExit(f"compressed image is {total} bytes (limit {args.max_total})")

    config = {}
    if args.image_config:
        config = json.loads(args.image_config.read_text(encoding="utf-8"))
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise SystemExit("deployment config must be linux/amd64")
        labels = config.get("config", {}).get("Labels") or {}
        for required in ("org.opencontainers.image.source", "org.opencontainers.image.revision", "org.opencontainers.image.licenses"):
            if not labels.get(required):
                raise SystemExit(f"missing OCI label {required}")
        exposed = config.get("config", {}).get("ExposedPorts") or {}
        if set(exposed) != {"22/tcp"}:
            raise SystemExit(f"only 22/tcp may be exposed in image metadata: {sorted(exposed)}")

    report = {
        "schema": 1,
        "media_type": media,
        "layer_count": len(layers),
        "largest_compressed_layer_bytes": largest,
        "total_compressed_bytes": total,
        "layers": [{"digest": item["digest"], "size": item["size"], "mediaType": item["mediaType"]} for item in layers],
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
