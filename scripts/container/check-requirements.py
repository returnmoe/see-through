#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REQ = ROOT / "requirements-image"
PROHIBITED = {"kornia", "matplotlib", "opencv-python", "pyqt6", "scikit-learn", "scipy", "torchaudio"}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def exact_requirements(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^ ;\\]+)", line)
        if not match:
            raise SystemExit(f"{path}:{number}: direct requirements must use ==")
        name, version = normalize(match.group(1)), match.group(2)
        if name in values:
            raise SystemExit(f"{path}:{number}: duplicate package {name}")
        values[name] = version
    return values


runtime = exact_requirements(REQ / "runtime.txt")
resolved = exact_requirements(REQ / "runtime-resolved.lock")
for name, version in runtime.items():
    if resolved.get(name) != version:
        raise SystemExit(f"runtime-resolved.lock does not pin {name}=={version}")
for name in sorted(PROHIBITED & runtime.keys()):
    raise SystemExit(f"image runtime unexpectedly includes {name}")

for lock in sorted(REQ.glob("*.lock")):
    if lock.name == "runtime-resolved.lock":
        continue
    text = lock.read_text(encoding="utf-8")
    packages = exact_requirements(lock)
    if not packages or text.count("--hash=sha256:") != len(packages):
        raise SystemExit(f"{lock}: every wheel must have exactly one SHA-256 hash")

dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
for variable in ("NODE_IMAGE", "CUDA_IMAGE"):
    if not re.search(rf"^ARG {variable}=[^\s]+@sha256:[0-9a-f]{{64}}$", dockerfile, re.MULTILINE):
        raise SystemExit(f"Docker base ARG {variable} is not digest-pinned")
for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
    for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
        match = re.search(r"\buses:\s*([^\s#]+)", line)
        if match and not re.search(r"@[0-9a-f]{40}$", match.group(1)):
            raise SystemExit(f"{workflow}:{number}: action must be pinned to a full commit SHA")

promotion = (ROOT / "scripts/container/promote-image.sh").read_text(encoding="utf-8")
if "imagetools create --prefer-index=false" not in promotion:
    raise SystemExit("image promotion must preserve a plain deployment manifest")
print("container dependency policy: OK")
