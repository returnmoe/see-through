from __future__ import annotations

import time
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from seethrough_server.app import create_app
from seethrough_server.config import Settings


class FakeGPU:
    def inspect(self) -> dict[str, Any]:
        return {
            "available": True,
            "name": "Fake GPU",
            "memory_mib": 24 * 1024,
            "memory_gib": 24.0,
            "driver_version": "test",
        }


class FakeStream:
    def __init__(self) -> None:
        self.lines = [
            b"Building LayerDiff3D pipeline...\n",
            b"Running LayerDiff3D...\n",
            b"Running Marigold depth...\n",
            b"Running PSD assembly...\n",
        ]

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


class FakeProcess:
    pid = 987654

    def __init__(self) -> None:
        self.stdout = FakeStream()
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0


def png_bytes(width: int = 32, height: int = 32) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (40, 80, 120)).save(output, "PNG")
    return output.getvalue()


def monochrome_png_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("1", (width, height), 0).save(output, "PNG")
    return output.getvalue()


def make_settings(tmp_path: Path, host: str = "127.0.0.1") -> Settings:
    settings = Settings.from_env(
        {
            "SEE_THROUGH_BIND_HOST": host,
            "SEE_THROUGH_DATA_DIR": str(tmp_path / "data"),
            "SEE_THROUGH_MODEL_CACHE": str(tmp_path / "models"),
            "SEE_THROUGH_PREFETCH": "off",
        },
        repo_root=tmp_path,
        web_dist=tmp_path / "missing-web",
    )
    return replace(settings, model_reserve_bytes=0)


def make_app(tmp_path: Path, host: str = "127.0.0.1"):
    def download(repo_id: str, revision: str, cache: Path) -> str:
        path = cache / revision
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    async def process_factory(*args, **kwargs):
        command = list(args)
        output = Path(command[command.index("--save_dir") + 1])
        source = Path(command[command.index("--srcp") + 1])
        result = output / source.stem
        result.mkdir(parents=True, exist_ok=True)
        (result / "reconstruction.png").write_bytes(png_bytes())
        (output.parent / "output.psd").write_bytes(b"8BPS")
        return FakeProcess()

    app = create_app(
        make_settings(tmp_path, host),
        model_downloader=download,
        gpu_probe=FakeGPU(),
        process_factory=process_factory,
    )
    for bundle in app.state.models.lock_manifest["bundles"].values():
        bundle["expected_size_bytes"] = 1
    return app


def test_security_headers_and_loopback_host_filter(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path), base_url="http://localhost") as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        rejected = client.get("/healthz", headers={"host": "pod.example"})
        assert rejected.status_code == 400


def test_explicit_wildcard_bind_accepts_external_host(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path, "0.0.0.0"), base_url="http://pod.example") as client:
        assert client.get("/healthz").status_code == 200


def test_mutations_require_client_header_and_same_origin(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path), base_url="http://localhost") as client:
        files = {"file": ("input.png", png_bytes(), "image/png")}
        assert client.post("/api/jobs", files=files).status_code == 403
        response = client.post(
            "/api/jobs",
            files={"file": ("input.png", png_bytes(), "image/png")},
            headers={"x-see-through-client": "web", "origin": "https://evil.example"},
        )
        assert response.status_code == 403


def test_upload_validation_and_stubbed_job_completion(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    headers = {"x-see-through-client": "web"}
    with TestClient(app, base_url="http://localhost") as client:
        mismatch = client.post(
            "/api/jobs",
            files={"file": ("input.jpg", png_bytes(), "image/jpeg")},
            headers=headers,
        )
        assert mismatch.status_code == 415

        created = client.post(
            "/api/jobs",
            files={"file": ("portrait.png", png_bytes(), "image/png")},
            data={
                "profile": "auto",
                "resolution": "10240",
                "seed": "4294967295",
                "steps": "77",
                "depth_resolution": "2048",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["config"] == {
            "profile": "auto",
            "selected_profile": None,
            "resolution": 10240,
            "seed": 4294967295,
            "steps": 77,
            "depth_resolution": 2048,
        }
        job_id = created.json()["id"]
        deadline = time.monotonic() + 3
        job = created.json()
        while time.monotonic() < deadline and job["state"] not in {
            "completed",
            "failed",
        }:
            time.sleep(0.02)
            job = client.get(f"/api/jobs/{job_id}").json()
        assert job["state"] == "completed", job
        assert job["profile_selected"] == "bf16"
        assert {item["kind"] for item in job["artifacts"]} >= {
            "psd",
            "reconstruction",
            "archive",
        }
        artifact = job["artifacts"][0]
        assert client.get(artifact["url"]).status_code == 200


def test_invalid_image_dimensions_and_job_options(tmp_path: Path) -> None:
    settings = replace(make_settings(tmp_path), max_image_dimension=16)
    app = create_app(settings, model_downloader=lambda *_: "", gpu_probe=FakeGPU())
    headers = {"x-see-through-client": "web"}
    with TestClient(app, base_url="http://localhost") as client:
        too_large = client.post(
            "/api/jobs",
            files={"file": ("wide.png", png_bytes(17, 1), "image/png")},
            headers=headers,
        )
        assert too_large.status_code == 413
        assert too_large.json()["detail"] == "Image dimensions exceed 16×16"
        invalid_profile = client.post(
            "/api/jobs",
            files={"file": ("ok.png", png_bytes(), "image/png")},
            data={"profile": "shell"},
            headers=headers,
        )
        assert invalid_profile.status_code == 422


def test_10000_square_upload_is_allowed_but_dimension_limit_is_enforced(
    tmp_path: Path,
) -> None:
    app = make_app(tmp_path)
    headers = {"x-see-through-client": "web"}
    with TestClient(app, base_url="http://localhost") as client:
        accepted = client.post(
            "/api/jobs",
            files={
                "file": (
                    "large.png",
                    monochrome_png_bytes(10000, 10000),
                    "image/png",
                )
            },
            headers=headers,
        )
        assert accepted.status_code == 201, accepted.text

        rejected = client.post(
            "/api/jobs",
            files={
                "file": (
                    "oversized.png",
                    monochrome_png_bytes(10241, 1),
                    "image/png",
                )
            },
            headers=headers,
        )
        assert rejected.status_code == 413
        assert rejected.json()["detail"] == "Image dimensions exceed 10240×10240"


def test_job_option_boundaries_and_alignment_are_validated(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    headers = {"x-see-through-client": "web"}
    invalid_options = (
        {"resolution": "704"},
        {"resolution": "10241"},
        {"resolution": "800"},
        {"seed": "-1"},
        {"seed": "4294967296"},
        {"steps": "0"},
        {"steps": "101"},
        {"depth_resolution": "192"},
        {"depth_resolution": "2112"},
        {"depth_resolution": "300"},
    )
    with TestClient(app, base_url="http://localhost") as client:
        for options in invalid_options:
            response = client.post(
                "/api/jobs",
                files={"file": ("input.png", png_bytes(), "image/png")},
                data=options,
                headers=headers,
            )
            assert response.status_code == 422, (options, response.text)


def test_prefetch_endpoint_is_nonblocking(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path), base_url="http://localhost") as client:
        response = client.post(
            "/api/models/prefetch",
            json={"selection": "nf4"},
            headers={"x-see-through-client": "web"},
        )
        assert response.status_code == 202
        assert response.json() == {"selection": "nf4", "status": "started"}
