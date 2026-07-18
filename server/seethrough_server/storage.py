from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})
RECOVERABLE_STATES = frozenset({"queued", "waiting_for_models"})


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_job_id(job_id: str) -> str:
    try:
        parsed = uuid.UUID(job_id)
    except (ValueError, AttributeError) as exc:
        raise KeyError(job_id) from exc
    if str(parsed) != job_id:
        raise KeyError(job_id)
    return job_id


class JobStore:
    def __init__(self, data_dir: Path, *, log_tail_lines: int = 200) -> None:
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.log_tail_lines = log_tail_lines
        self._lock = threading.RLock()

    def initialize(self) -> list[dict[str, Any]]:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        recovered: list[dict[str, Any]] = []
        with self._lock:
            for record in self.list(raw=True):
                if record["state"] == "running":
                    record.update(
                        state="interrupted",
                        phase="interrupted",
                        progress=None,
                        error="The service restarted while inference was running.",
                        finished_at=utc_now(),
                        cancel_requested=False,
                    )
                    self._save(record)
                elif record["state"] in RECOVERABLE_STATES:
                    record.update(
                        state="queued",
                        phase="queued",
                        progress=0,
                        cancel_requested=False,
                    )
                    self._save(record)
                    recovered.append(record)
        return recovered

    def create(
        self,
        *,
        staged_input: Path,
        original_filename: str,
        extension: str,
        profile: str,
        resolution: int,
        seed: int = 42,
        steps: int = 30,
        depth_resolution: int = 768,
    ) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = utc_now()
        job_dir = self.jobs_dir / job_id
        input_dir = job_dir / "input"
        output_dir = job_dir / "output"
        input_dir.mkdir(parents=True, mode=0o770)
        output_dir.mkdir(mode=0o770)
        input_name = f"{uuid.uuid4().hex}{extension.lower()}"
        input_path = input_dir / input_name
        os.replace(staged_input, input_path)
        record: dict[str, Any] = {
            "schema": 1,
            "id": job_id,
            "state": "queued",
            "phase": "queued",
            "progress": 0,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "original_filename": original_filename,
            "input_file": str(input_path.relative_to(job_dir)),
            "profile_requested": profile,
            "profile_selected": None,
            "resolution": resolution,
            "seed": seed,
            "steps": steps,
            "depth_resolution": depth_resolution,
            "error": None,
            "cancel_requested": False,
            "artifacts": [],
        }
        with self._lock:
            self._save(record)
        return self.view(record)

    def list(self, *, raw: bool = False) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if not self.jobs_dir.exists():
            return records
        with self._lock:
            for path in self.jobs_dir.glob("*/job.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    _canonical_job_id(record["id"])
                except (OSError, json.JSONDecodeError, KeyError, TypeError):
                    continue
                records.append(record if raw else self.view(record))
        return sorted(records, key=lambda item: item["created_at"], reverse=True)

    def get(self, job_id: str, *, raw: bool = False) -> dict[str, Any]:
        job_id = _canonical_job_id(job_id)
        path = self.jobs_dir / job_id / "job.json"
        with self._lock:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                raise KeyError(job_id) from exc
        return record if raw else self.view(record)

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            record = self.get(job_id, raw=True)
            record.update(changes)
            record["updated_at"] = utc_now()
            self._save(record)
            return self.view(record)

    def append_log(self, job_id: str, line: str) -> None:
        job_dir = self.job_dir(job_id)
        log_path = job_dir / "job.log"
        clean = line.replace("\x00", "").rstrip("\r\n")
        with self._lock, log_path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(clean + "\n")
            handle.flush()

    def view(self, record: dict[str, Any]) -> dict[str, Any]:
        value = deepcopy(record)
        value.setdefault("seed", 42)
        value.setdefault("steps", 30)
        value.setdefault("depth_resolution", 768)
        log_path = self.jobs_dir / record["id"] / "job.log"
        log_tail = self._tail(log_path)
        value["log_tail"] = log_tail
        value["logs"] = log_tail
        value["status"] = record["state"]
        value["input_name"] = record["original_filename"]
        value["config"] = {
            "profile": record["profile_requested"],
            "selected_profile": record.get("profile_selected"),
            "resolution": record["resolution"],
            "seed": record.get("seed", 42),
            "steps": record.get("steps", 30),
            "depth_resolution": record.get("depth_resolution", 768),
        }
        public_artifacts = []
        for artifact in value.get("artifacts", []):
            public_artifact = {key: item for key, item in artifact.items() if key != "path"}
            public_artifact["size"] = artifact["size_bytes"]
            public_artifact["mime"] = artifact["content_type"]
            public_artifact["url"] = (
                f"/api/jobs/{record['id']}/artifacts/{artifact['id']}"
            )
            public_artifacts.append(public_artifact)
        value["artifacts"] = public_artifacts
        return value

    def next_queued(self) -> dict[str, Any] | None:
        queued = [item for item in self.list(raw=True) if item["state"] == "queued"]
        return min(queued, key=lambda item: item["created_at"]) if queued else None

    def delete(self, job_id: str) -> None:
        record = self.get(job_id, raw=True)
        if record["state"] not in TERMINAL_STATES:
            raise RuntimeError("Only terminal jobs can be deleted")
        job_dir = self.job_dir(job_id)
        with self._lock:
            shutil.rmtree(job_dir)

    def job_dir(self, job_id: str) -> Path:
        job_id = _canonical_job_id(job_id)
        path = (self.jobs_dir / job_id).resolve()
        if path.parent != self.jobs_dir.resolve():
            raise KeyError(job_id)
        return path

    def input_path(self, record: dict[str, Any]) -> Path:
        job_dir = self.job_dir(record["id"])
        path = (job_dir / record["input_file"]).resolve()
        if not path.is_relative_to(job_dir) or not path.is_file():
            raise FileNotFoundError(record["input_file"])
        return path

    def artifact_path(self, job_id: str, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        record = self.get(job_id, raw=True)
        artifact = next(
            (item for item in record.get("artifacts", []) if item["id"] == artifact_id),
            None,
        )
        if artifact is None:
            raise KeyError(artifact_id)
        job_dir = self.job_dir(job_id)
        path = (job_dir / artifact["path"]).resolve()
        if not path.is_relative_to(job_dir) or not path.is_file():
            raise KeyError(artifact_id)
        return path, artifact

    def _save(self, record: dict[str, Any]) -> None:
        path = self.jobs_dir / record["id"] / "job.json"
        _atomic_json(path, record)

    def _tail(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        return [line.rstrip("\r\n") for line in lines[-self.log_tail_lines :]]


def write_atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_json(path, value)
