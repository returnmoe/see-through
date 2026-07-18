from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import signal
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .config import Settings
from .model_cache import GPUProbe, ModelManager, bundle_for_profile, select_profile
from .storage import JobStore, TERMINAL_STATES, utc_now

PROFILES = frozenset({"auto", "bf16", "group-offload", "nf4", "blockswap"})
DEFAULT_RESOLUTION = 1280
MIN_RESOLUTION = 768
MAX_RESOLUTION = 10240
RESOLUTION_STEP = 64
DEFAULT_SEED = 42
MIN_SEED = 0
MAX_SEED = 2**32 - 1
DEFAULT_STEPS = 30
MIN_STEPS = 1
MAX_STEPS = 100
DEFAULT_DEPTH_RESOLUTION = 768
MIN_DEPTH_RESOLUTION = 256
MAX_DEPTH_RESOLUTION = 2048
DEPTH_RESOLUTION_STEP = 64
INFERENCE_HEARTBEAT_SECONDS = 30.0

ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

PHASE_MARKERS = (
    ("building layerdiff", "loading_layerdiff", 15),
    ("running layerdiff", "layerdiff", 30),
    ("running layerdiff3d", "layerdiff", 30),
    ("building marigold", "loading_marigold", 55),
    ("running marigold", "marigold", 65),
    ("running psd assembly", "psd_assembly", 85),
    ("psd saved", "packaging", 92),
)

ARTIFACT_EXTENSIONS = frozenset(
    {".psd", ".json", ".png", ".jpg", ".jpeg", ".webp", ".jxl", ".txt", ".zip"}
)


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    async def publish(self, event: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)


def build_inference_command(
    settings: Settings,
    record: dict[str, Any],
    profile: str,
    snapshots: dict[str, str],
) -> list[str]:
    job_dir = settings.data_dir / "jobs" / record["id"]
    input_path = job_dir / record["input_file"]
    save_dir = job_dir / "output"
    seed = record.get("seed", DEFAULT_SEED)
    steps = record.get("steps", DEFAULT_STEPS)
    depth_resolution = record.get(
        "depth_resolution", DEFAULT_DEPTH_RESOLUTION
    )
    common = [
        "--srcp",
        str(input_path),
        "--save_dir",
        str(save_dir),
        "--seed",
        str(seed),
        "--resolution",
        str(record["resolution"]),
        "--resolution_depth",
        str(depth_resolution),
        "--save_to_psd",
    ]
    if record.get("tblr_split", False):
        common.append("--tblr_split")
    if profile in {"bf16", "group-offload"}:
        command = [
            sys.executable,
            str(settings.repo_root / "inference/scripts/inference_psd.py"),
            *common,
            "--inference_steps",
            str(steps),
            "--disable_progressbar",
            "--repo_id_layerdiff",
            snapshots["layerdiff"],
            "--repo_id_depth",
            snapshots["depth"],
        ]
        if profile == "group-offload":
            command.append("--group_offload")
        return command
    if profile == "nf4":
        return [
            sys.executable,
            str(settings.repo_root / "inference/scripts/inference_psd_quantized.py"),
            *common,
            "--num_inference_steps",
            str(steps),
            "--quant_mode",
            "nf4",
            "--disable_progressbar",
            "--repo_id_layerdiff",
            snapshots["layerdiff"],
            "--repo_id_depth",
            snapshots["depth"],
        ]
    if profile == "blockswap":
        return [
            sys.executable,
            str(settings.repo_root / "inference/scripts/inference_psd_blockswap.py"),
            *common,
            "--num_inference_steps",
            str(steps),
            "--disable_progressbar",
            "--repo_id_layerdiff",
            snapshots["layerdiff"],
            "--repo_id_depth",
            snapshots["depth"],
        ]
    raise ValueError(f"Unsupported selected profile: {profile}")


class JobManager:
    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        models: ModelManager,
        *,
        gpu_probe: GPUProbe | None = None,
        process_factory: Any = None,
        heartbeat_interval: float = INFERENCE_HEARTBEAT_SECONDS,
    ) -> None:
        self.settings = settings
        self.store = store
        self.models = models
        self.gpu_probe = gpu_probe or GPUProbe()
        self.process_factory = process_factory or asyncio.create_subprocess_exec
        self.heartbeat_interval = heartbeat_interval
        self.events = EventBroker()
        self._wake = asyncio.Event()
        self._worker_task: asyncio.Task[None] | None = None
        self._prefetch_task: asyncio.Task[None] | None = None
        self._current_job: str | None = None
        self._current_process: Any = None
        self._closing = False

    async def start(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings.model_cache.mkdir(parents=True, exist_ok=True)
        recovered = self.store.initialize()
        self._worker_task = asyncio.create_task(self._worker(), name="inference-worker")
        if recovered:
            self._wake.set()
        if self.settings.prefetch != "off":
            self._prefetch_task = asyncio.create_task(
                self.prefetch(self.settings.prefetch), name="model-prefetch"
            )

    async def close(self) -> None:
        self._closing = True
        if self._current_process and self._current_process.returncode is None:
            await self._terminate_process(self._current_process)
        for task in (self._worker_task, self._prefetch_task):
            if task:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._worker_task, self._prefetch_task) if task),
            return_exceptions=True,
        )

    async def notify_job(self, job_id: str) -> None:
        await self.events.publish({"type": "job", "job": self.store.get(job_id)})

    def enqueue(self) -> None:
        self._wake.set()

    async def cancel(self, job_id: str) -> dict[str, Any]:
        record = self.store.get(job_id, raw=True)
        if record["state"] in TERMINAL_STATES:
            return self.store.view(record)
        if record["state"] == "queued":
            updated = self.store.update(
                job_id,
                state="cancelled",
                phase="cancelled",
                progress=None,
                finished_at=utc_now(),
                cancel_requested=True,
            )
            await self.notify_job(job_id)
            return updated
        updated = self.store.update(job_id, cancel_requested=True)
        if self._current_job == job_id and self._current_process:
            await self._terminate_process(self._current_process)
        await self.notify_job(job_id)
        return updated

    async def prefetch(self, selection: str) -> dict[str, str] | None:
        bundle = selection
        if selection == "auto":
            gpu = await asyncio.to_thread(self.gpu_probe.inspect)
            memory = int(gpu.get("memory_mib", 0))
            if memory < 8 * 1024:
                await self.events.publish(
                    {"type": "models", "bundle": None, "state": "no_compatible_gpu"}
                )
                return None
            bundle = "bf16" if memory >= 11 * 1024 else "nf4"
        if bundle not in {"bf16", "nf4"}:
            raise ValueError("Prefetch selection must be auto, bf16, or nf4")
        return await self.models.ensure_bundle(bundle, notify=self.events.publish)

    async def _worker(self) -> None:
        while not self._closing:
            record = self.store.next_queued()
            if record is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            await self._run(record)

    async def _run(self, record: dict[str, Any]) -> None:
        job_id = record["id"]
        self._current_job = job_id
        try:
            gpu = await asyncio.to_thread(self.gpu_probe.inspect)
            if not gpu.get("available"):
                raise RuntimeError("No NVIDIA GPU is available")
            profile = select_profile(record["profile_requested"], gpu["memory_mib"])
            bundle = bundle_for_profile(profile)
            self.store.update(
                job_id,
                state="waiting_for_models",
                phase="model_download",
                progress=5,
                profile_selected=profile,
            )
            await self.notify_job(job_id)
            snapshots = await self.models.ensure_bundle(bundle, notify=self.events.publish)
            if self.store.get(job_id, raw=True).get("cancel_requested"):
                self.store.update(
                    job_id,
                    state="cancelled",
                    phase="cancelled",
                    progress=None,
                    finished_at=utc_now(),
                )
                await self.notify_job(job_id)
                return

            command = build_inference_command(self.settings, record, profile, snapshots)
            self.store.update(
                job_id,
                state="running",
                phase="starting",
                progress=10,
                started_at=utc_now(),
            )
            self._record_inference_log(job_id, "Starting inference")
            await self.notify_job(job_id)

            environment = os.environ.copy()
            environment.update(
                {
                    "HF_HOME": str(self.settings.model_cache),
                    "HF_HUB_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "DIFFUSERS_OFFLINE": "1",
                    "DIFFUSERS_VERBOSITY": "error",
                    "PYTHONUNBUFFERED": "1",
                }
            )
            process = await self.process_factory(
                *command,
                cwd=str(self.settings.repo_root),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            self._current_process = process
            await self._stream_process_output(job_id, process)
            return_code = await process.wait()
            current = self.store.get(job_id, raw=True)
            if current.get("cancel_requested"):
                self.store.update(
                    job_id,
                    state="cancelled",
                    phase="cancelled",
                    progress=None,
                    finished_at=utc_now(),
                )
            elif return_code != 0:
                raise RuntimeError(f"Inference exited with status {return_code}")
            else:
                self._record_inference_log(job_id, "Inference completed; packaging artifacts")
                self.store.update(job_id, phase="packaging", progress=95)
                artifacts = await asyncio.to_thread(self._package_artifacts, job_id)
                self.store.update(
                    job_id,
                    state="completed",
                    phase="completed",
                    progress=100,
                    finished_at=utc_now(),
                    artifacts=artifacts,
                )
            await self.notify_job(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_inference_log(job_id, f"ERROR: {exc}")
            self.store.update(
                job_id,
                state="failed",
                phase="failed",
                progress=None,
                error=str(exc),
                finished_at=utc_now(),
            )
            await self.notify_job(job_id)
        finally:
            self._current_job = None
            self._current_process = None

    def _record_inference_log(self, job_id: str, line: str) -> str:
        clean = ANSI_ESCAPE.sub("", line).replace("\r", "").rstrip("\n")
        if not clean:
            return ""
        self.store.append_log(job_id, clean)
        print(f"[inference {job_id[:8]}] {clean}", flush=True)
        return clean

    async def _stream_process_output(self, job_id: str, process: Any) -> None:
        assert process.stdout is not None
        loop = asyncio.get_running_loop()
        started = loop.time()
        last_output = started
        read_task = asyncio.create_task(process.stdout.readline())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {read_task},
                    timeout=self.heartbeat_interval,
                )
                now = loop.time()
                if not done:
                    current = self.store.get(job_id, raw=True)
                    phase = str(current.get("phase") or "inference").replace("_", " ")
                    elapsed = round(now - started)
                    quiet = round(now - last_output)
                    self._record_inference_log(
                        job_id,
                        f"Heartbeat: {phase} is still running "
                        f"({elapsed}s elapsed, no output for {quiet}s)",
                    )
                    await self.notify_job(job_id)
                    continue

                line = read_task.result()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                clean = self._record_inference_log(job_id, text)
                if clean:
                    last_output = now
                    phase_changed = await self._phase_from_line(job_id, clean)
                    if not phase_changed:
                        await self.notify_job(job_id)
                read_task = asyncio.create_task(process.stdout.readline())
        finally:
            if not read_task.done():
                read_task.cancel()
                await asyncio.gather(read_task, return_exceptions=True)

    async def _phase_from_line(self, job_id: str, line: str) -> bool:
        lower = line.lower()
        for marker, phase, progress in PHASE_MARKERS:
            if marker in lower:
                current = self.store.get(job_id, raw=True)
                if current.get("phase") != phase:
                    self.store.update(job_id, phase=phase, progress=progress)
                    await self.notify_job(job_id)
                return True
        return False

    async def _terminate_process(self, process: Any) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    def _package_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        job_dir = self.store.job_dir(job_id)
        candidates: list[Path] = []
        excluded = {job_dir / "job.json", job_dir / "job.log"}
        input_dir = job_dir / "input"
        for path in job_dir.rglob("*"):
            if (
                path.is_file()
                and path not in excluded
                and not path.is_relative_to(input_dir)
                and path.suffix.lower() in ARTIFACT_EXTENSIONS
                and path.name != "artifacts.zip"
            ):
                candidates.append(path)
        log_path = job_dir / "job.log"
        if log_path.is_file():
            candidates.append(log_path)
        candidates.sort(key=lambda path: str(path.relative_to(job_dir)))
        if not candidates:
            raise RuntimeError("Inference completed without producing artifacts")

        zip_path = job_dir / "artifacts.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in candidates:
                archive.write(path, arcname=str(path.relative_to(job_dir)))
        candidates.append(zip_path)

        artifacts: list[dict[str, Any]] = []
        for path in candidates:
            relative = path.relative_to(job_dir)
            artifacts.append(
                {
                    "id": uuid.uuid5(uuid.UUID(job_id), str(relative)).hex,
                    "name": path.name,
                    "path": str(relative),
                    "kind": _artifact_kind(path),
                    "size_bytes": path.stat().st_size,
                    "content_type": mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream",
                }
            )
        return artifacts


def _artifact_kind(path: Path) -> str:
    name = path.name.lower()
    if name == "artifacts.zip":
        return "archive"
    if name == "job.log":
        return "log"
    if name == "reconstruction.png":
        return "reconstruction"
    if name.endswith("_depth.psd"):
        return "depth_psd"
    if path.suffix.lower() == ".psd":
        return "psd"
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".jxl"}:
        return "layer"
    if name == "stats.json":
        return "stats"
    return "metadata"
