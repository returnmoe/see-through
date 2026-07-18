from __future__ import annotations

import asyncio
import json
import shutil
import uuid
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings
from .model_cache import GPUProbe, ModelManager
from .runner import (
    DEFAULT_DEPTH_RESOLUTION,
    DEFAULT_RESOLUTION,
    DEFAULT_SEED,
    DEFAULT_STEPS,
    DEPTH_RESOLUTION_STEP,
    JobManager,
    MAX_DEPTH_RESOLUTION,
    MAX_RESOLUTION,
    MAX_SEED,
    MAX_STEPS,
    MIN_DEPTH_RESOLUTION,
    MIN_RESOLUTION,
    MIN_SEED,
    MIN_STEPS,
    PROFILES,
    RESOLUTION_STEP,
)
from .security import SecurityMiddleware
from .storage import JobStore, TERMINAL_STATES

ALLOWED_UPLOADS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    ".bmp": (frozenset({"image/bmp", "image/x-ms-bmp"}), frozenset({"BMP"})),
    ".jpg": (frozenset({"image/jpeg"}), frozenset({"JPEG"})),
    ".jpeg": (frozenset({"image/jpeg"}), frozenset({"JPEG"})),
    ".png": (frozenset({"image/png"}), frozenset({"PNG"})),
    ".webp": (frozenset({"image/webp"}), frozenset({"WEBP"})),
    ".jxl": (frozenset({"image/jxl"}), frozenset({"JXL", "JPEGXL"})),
}


class PrefetchRequest(BaseModel):
    selection: Literal["auto", "bf16", "nf4"] = "auto"


def create_app(
    settings: Settings | None = None,
    *,
    model_downloader: Any = None,
    gpu_probe: GPUProbe | None = None,
    process_factory: Any = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    store = JobStore(config.data_dir)
    models = ModelManager(config, downloader=model_downloader)
    probe = gpu_probe or GPUProbe()
    jobs = JobManager(
        config,
        store,
        models,
        gpu_probe=probe,
        process_factory=process_factory,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = config
        application.state.store = store
        application.state.models = models
        application.state.jobs = jobs
        await jobs.start()
        try:
            yield
        finally:
            await jobs.close()

    application = FastAPI(
        title="See-through",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = config
    application.state.store = store
    application.state.models = models
    application.state.jobs = jobs
    application.add_middleware(SecurityMiddleware, settings=config)

    @application.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/system")
    async def system() -> dict[str, Any]:
        gpu = await asyncio.to_thread(probe.inspect)
        disk = shutil.disk_usage(config.data_dir)
        records = store.list(raw=True)
        counts: dict[str, int] = {}
        for record in records:
            counts[record["state"]] = counts.get(record["state"], 0) + 1
        running = next(
            (record["id"] for record in records if record["state"] == "running"),
            None,
        )
        return {
            "version": "0.1.0",
            "gpu": gpu,
            "models": models.status(),
            "storage": {
                "data_dir": str(config.data_dir),
                "disk_free_bytes": disk.free,
                "disk_total_bytes": disk.total,
            },
            "queue": {"counts": counts, "running_job_id": running},
            "listener": {
                "host": config.bind_host,
                "port": config.port,
                "loopback_only": config.loopback_only,
            },
        }

    @application.get("/api/jobs")
    async def list_jobs() -> list[dict[str, Any]]:
        return store.list()

    @application.post("/api/jobs", status_code=status.HTTP_201_CREATED)
    async def create_job(
        file: UploadFile = File(...),
        profile: str = Form("auto"),
        resolution: int = Form(DEFAULT_RESOLUTION),
        seed: int = Form(DEFAULT_SEED),
        steps: int = Form(DEFAULT_STEPS),
        depth_resolution: int = Form(DEFAULT_DEPTH_RESOLUTION),
        tblr_split: bool = Form(False),
    ) -> dict[str, Any]:
        if profile not in PROFILES:
            raise HTTPException(422, f"profile must be one of: {', '.join(sorted(PROFILES))}")
        if (
            not MIN_RESOLUTION <= resolution <= MAX_RESOLUTION
            or resolution % RESOLUTION_STEP
        ):
            raise HTTPException(
                422,
                f"resolution must be a multiple of {RESOLUTION_STEP} between "
                f"{MIN_RESOLUTION} and {MAX_RESOLUTION}",
            )
        if not MIN_SEED <= seed <= MAX_SEED:
            raise HTTPException(
                422, f"seed must be between {MIN_SEED} and {MAX_SEED}"
            )
        if not MIN_STEPS <= steps <= MAX_STEPS:
            raise HTTPException(
                422, f"steps must be between {MIN_STEPS} and {MAX_STEPS}"
            )
        if (
            not MIN_DEPTH_RESOLUTION
            <= depth_resolution
            <= MAX_DEPTH_RESOLUTION
            or depth_resolution % DEPTH_RESOLUTION_STEP
        ):
            raise HTTPException(
                422,
                "depth_resolution must be a multiple of "
                f"{DEPTH_RESOLUTION_STEP} between {MIN_DEPTH_RESOLUTION} and "
                f"{MAX_DEPTH_RESOLUTION}",
            )
        original = Path(file.filename or "")
        extension = original.suffix.lower()
        if extension not in ALLOWED_UPLOADS or original.name in {"", ".", ".."}:
            raise HTTPException(415, "Unsupported image extension")
        accepted_mimes, _ = ALLOWED_UPLOADS[extension]
        if file.content_type not in accepted_mimes:
            raise HTTPException(415, "The upload MIME type does not match its extension")

        staging = config.data_dir / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        staged = staging / f"{uuid.uuid4().hex}.upload"
        try:
            size = 0
            with staged.open("xb") as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > config.max_upload_bytes:
                        raise HTTPException(413, "Image exceeds the 50 MiB upload limit")
                    handle.write(chunk)
            if size == 0:
                raise HTTPException(422, "Image is empty")
            await asyncio.to_thread(_validate_image, staged, extension, config)
            created = store.create(
                staged_input=staged,
                original_filename=original.name,
                extension=extension,
                profile=profile,
                resolution=resolution,
                seed=seed,
                steps=steps,
                depth_resolution=depth_resolution,
                tblr_split=tblr_split,
            )
        finally:
            staged.unlink(missing_ok=True)
            await file.close()
        jobs.enqueue()
        await jobs.notify_job(created["id"])
        return created

    @application.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        try:
            return store.get(job_id)
        except KeyError as exc:
            raise HTTPException(404, "Job not found") from exc

    @application.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            return await jobs.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(404, "Job not found") from exc

    @application.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_job(job_id: str) -> None:
        try:
            record = store.get(job_id, raw=True)
            if record["state"] not in TERMINAL_STATES:
                raise HTTPException(409, "Only terminal jobs can be deleted")
            store.delete(job_id)
        except KeyError as exc:
            raise HTTPException(404, "Job not found") from exc
        await jobs.events.publish({"type": "job_deleted", "job_id": job_id})

    @application.get("/api/jobs/{job_id}/artifacts/{artifact_id}")
    async def download_artifact(job_id: str, artifact_id: str) -> FileResponse:
        try:
            path, artifact = store.artifact_path(job_id, artifact_id)
        except KeyError as exc:
            raise HTTPException(404, "Artifact not found") from exc
        return FileResponse(
            path,
            media_type=artifact["content_type"],
            filename=artifact["name"],
        )

    @application.post("/api/models/prefetch", status_code=status.HTTP_202_ACCEPTED)
    async def prefetch_models(payload: PrefetchRequest) -> dict[str, str]:
        task = asyncio.create_task(jobs.prefetch(payload.selection))
        task.add_done_callback(_consume_task_exception)
        return {"selection": payload.selection, "status": "started"}

    @application.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            queue = jobs.events.subscribe()
            snapshot = {"jobs": store.list(), "models": models.status()}
            yield _sse("snapshot", snapshot)
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    event_name = event.get("type", "message")
                    yield _sse(event_name, event)
            finally:
                jobs.events.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if config.web_dist.is_dir() and (config.web_dist / "index.html").is_file():
        application.mount(
            "/", StaticFiles(directory=config.web_dist, html=True), name="web"
        )
    else:
        @application.get("/", include_in_schema=False)
        async def no_web_build() -> JSONResponse:
            return JSONResponse(
                {"service": "see-through", "web": "not-built"}, status_code=503
            )

    return application


def _validate_image(path: Path, extension: str, settings: Settings) -> None:
    try:
        import pillow_jxl  # noqa: F401
    except ImportError:
        if extension == ".jxl":
            raise HTTPException(415, "JPEG XL support is not installed") from None
    from PIL import Image, UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width, height = image.size
                detected = (image.format or "").upper()
                if width <= 0 or height <= 0:
                    raise HTTPException(422, "Image dimensions are invalid")
                if (
                    width > settings.max_image_dimension
                    or height > settings.max_image_dimension
                ):
                    limit = settings.max_image_dimension
                    raise HTTPException(
                        413, f"Image dimensions exceed {limit}×{limit}"
                    )
                max_pixels = settings.max_image_dimension**2
                if width * height > max_pixels:
                    raise HTTPException(
                        413, f"Image pixel count exceeds {max_pixels}"
                    )
                _, formats = ALLOWED_UPLOADS[extension]
                if detected not in formats:
                    raise HTTPException(415, "Decoded image format does not match extension")
                image.verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Image could not be decoded safely") from exc


def _sse(event: str, value: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(value, separators=(',', ':'))}\n\n"


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass


app = create_app()
