from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import Settings
from .storage import utc_now, write_atomic_json

Download = Callable[[str, str, Path], str]
Notify = Callable[[dict[str, Any]], Awaitable[None]]


def _default_download(repo_id: str, revision: str, cache_dir: Path) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(
        repo_id=repo_id,
        revision=revision,
        cache_dir=str(cache_dir),
        local_files_only=False,
    )


class GPUProbe:
    def inspect(self) -> dict[str, Any]:
        command = [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            devices = []
            for index, line in enumerate(result.stdout.strip().splitlines()):
                name, memory, driver = [
                    field.strip() for field in line.split(",", 2)
                ]
                memory_mib = int(float(memory))
                devices.append(
                    {
                        "index": index,
                        "name": name,
                        "memory_mib": memory_mib,
                        "memory_gib": round(memory_mib / 1024, 2),
                        "driver_version": driver,
                    }
                )
            first = devices[0]
            return {
                "available": True,
                "name": first["name"],
                "memory_mib": first["memory_mib"],
                "memory_gib": first["memory_gib"],
                "driver_version": first["driver_version"],
                "count": len(devices),
                "devices": devices,
            }
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return {
                "available": False,
                "name": None,
                "memory_mib": 0,
                "memory_gib": 0,
                "driver_version": None,
                "count": 0,
                "devices": [],
            }


class ModelManager:
    def __init__(
        self,
        settings: Settings,
        *,
        downloader: Download | None = None,
    ) -> None:
        self.settings = settings
        self.downloader = downloader or _default_download
        lock_text = files("seethrough_server").joinpath("model-lock.json").read_text()
        self.lock_manifest: dict[str, Any] = json.loads(lock_text)
        self.state_path = settings.model_cache / "see-through-models.json"
        self._locks = {name: asyncio.Lock() for name in self.lock_manifest["bundles"]}
        self._runtime: dict[str, dict[str, Any]] = {
            name: {"state": "not_downloaded", "error": None, "updated_at": None}
            for name in self.lock_manifest["bundles"]
        }
        self._load_state()

    def status(self) -> dict[str, Any]:
        disk = shutil.disk_usage(_existing_ancestor(self.settings.model_cache))
        bundles: dict[str, Any] = {}
        for name, spec in self.lock_manifest["bundles"].items():
            entry = dict(self._runtime[name])
            entry["expected_size_bytes"] = spec["expected_size_bytes"]
            entry["repositories"] = {
                role: {"repo_id": item["repo_id"], "revision": item["revision"]}
                for role, item in spec["repositories"].items()
            }
            bundles[name] = entry
        return {
            "cache_dir": str(self.settings.model_cache),
            "disk_free_bytes": disk.free,
            "reserve_bytes": self.settings.model_reserve_bytes,
            "bundles": bundles,
        }

    async def ensure_bundle(
        self, bundle: str, *, notify: Notify | None = None
    ) -> dict[str, str]:
        if bundle not in self.lock_manifest["bundles"]:
            raise ValueError(f"Unknown model bundle: {bundle}")
        async with self._locks[bundle]:
            paths = self._complete_paths(bundle)
            if paths is not None:
                return paths

            spec = self.lock_manifest["bundles"][bundle]
            free = shutil.disk_usage(self.settings.model_cache.parent).free
            required = spec["expected_size_bytes"] + self.settings.model_reserve_bytes
            if free < required:
                error = f"Insufficient model-cache space: need {required} bytes, have {free}"
                self._set_runtime(bundle, "failed", error)
                raise RuntimeError(error)

            self.settings.model_cache.mkdir(parents=True, exist_ok=True)
            self._set_runtime(bundle, "downloading", None)
            if notify:
                await notify({"type": "models", "bundle": bundle, "state": "downloading"})
            downloaded: dict[str, str] = {}
            try:
                for role, repository in spec["repositories"].items():
                    downloaded[role] = await asyncio.to_thread(
                        self.downloader,
                        repository["repo_id"],
                        repository["revision"],
                        self.settings.model_cache,
                    )
                    missing = self._missing_required_files(
                        Path(downloaded[role]),
                        repository,
                    )
                    if missing:
                        names = ", ".join(missing)
                        raise RuntimeError(
                            f"Downloaded {role} snapshot is incomplete; missing: {names}"
                        )
                self._set_runtime(bundle, "ready", None, paths=downloaded)
                if notify:
                    await notify({"type": "models", "bundle": bundle, "state": "ready"})
                return downloaded
            except Exception as exc:
                self._set_runtime(bundle, "failed", str(exc))
                if notify:
                    await notify(
                        {"type": "models", "bundle": bundle, "state": "failed"}
                    )
                raise

    def _load_state(self) -> None:
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        for bundle, value in saved.get("bundles", {}).items():
            if bundle not in self._runtime or value.get("state") != "ready":
                continue
            paths = value.get("paths", {})
            if self._paths_are_complete(bundle, paths):
                self._runtime[bundle] = value

    def _complete_paths(self, bundle: str) -> dict[str, str] | None:
        value = self._runtime[bundle]
        paths = value.get("paths", {})
        if value.get("state") == "ready" and self._paths_are_complete(bundle, paths):
            return dict(paths)
        return None

    def _paths_are_complete(self, bundle: str, paths: dict[str, str]) -> bool:
        repositories = self.lock_manifest["bundles"][bundle]["repositories"]
        if not isinstance(paths, dict) or not paths or set(paths) != set(repositories):
            return False
        return all(
            Path(paths[role]).is_dir()
            and not self._missing_required_files(Path(paths[role]), repository)
            for role, repository in repositories.items()
        )

    @staticmethod
    def _missing_required_files(
        snapshot: Path,
        repository: dict[str, Any],
    ) -> list[str]:
        return [
            relative
            for relative in repository.get("required_files", [])
            if not (snapshot / relative).is_file()
        ]

    def _set_runtime(
        self,
        bundle: str,
        state: str,
        error: str | None,
        *,
        paths: dict[str, str] | None = None,
    ) -> None:
        value: dict[str, Any] = {
            "state": state,
            "error": error,
            "updated_at": utc_now(),
        }
        if paths:
            value["paths"] = paths
        self._runtime[bundle] = value
        self.settings.model_cache.mkdir(parents=True, exist_ok=True)
        write_atomic_json(
            self.state_path,
            {"schema": 1, "bundles": self._runtime, "updated_at": utc_now()},
        )


def select_profile(requested: str, memory_mib: int) -> str:
    if requested != "auto":
        return requested
    if memory_mib >= 18 * 1024:
        return "bf16"
    if memory_mib >= 11 * 1024:
        return "group-offload"
    if memory_mib >= 8 * 1024:
        return "nf4"
    raise RuntimeError("At least 8 GiB of GPU memory is required")


def bundle_for_profile(profile: str) -> str:
    return "nf4" if profile == "nf4" else "bf16"


def _existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate
