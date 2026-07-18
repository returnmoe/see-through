from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from seethrough_server.config import Settings
from seethrough_server.model_cache import (
    GPUProbe,
    ModelManager,
    bundle_for_profile,
    select_profile,
)
from seethrough_server.runner import build_inference_command


def settings_for(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "SEE_THROUGH_DATA_DIR": str(tmp_path / "data"),
            "SEE_THROUGH_MODEL_CACHE": str(tmp_path / "models"),
            "SEE_THROUGH_PREFETCH": "off",
        },
        repo_root=tmp_path,
    )


def test_model_lock_contains_exact_revisions() -> None:
    lock = json.loads(
        (Path(__file__).parents[1] / "seethrough_server/model-lock.json").read_text()
    )
    assert lock["bundles"]["bf16"]["repositories"]["layerdiff"]["revision"] == (
        "966721bb4ef2ddc3af3696862fa10b3f78d9785d"
    )
    assert lock["bundles"]["bf16"]["repositories"]["depth"]["revision"] == (
        "aa7a892f83ff68d7b09186a405ba08d5d33f770f"
    )
    assert lock["bundles"]["nf4"]["repositories"]["layerdiff"]["revision"] == (
        "39b9881340189810bebabe5462756fb2e8fbd5fa"
    )
    assert lock["bundles"]["nf4"]["repositories"]["depth"]["revision"] == (
        "aad13aafa9f3c72defb40a9d9225cec70b0eab16"
    )


@pytest.mark.asyncio
async def test_prefetch_uses_pinned_revisions_and_reuses_completed_paths(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    def download(repo_id: str, revision: str, cache: Path) -> str:
        calls.append((repo_id, revision))
        destination = cache / revision
        destination.mkdir(parents=True, exist_ok=True)
        return str(destination)

    manager = ModelManager(
        replace(settings_for(tmp_path), model_reserve_bytes=0), downloader=download
    )
    manager.lock_manifest["bundles"]["nf4"]["expected_size_bytes"] = 1
    first = await manager.ensure_bundle("nf4")
    second = await manager.ensure_bundle("nf4")
    assert first == second
    assert len(calls) == 2
    assert calls[0][1] == "39b9881340189810bebabe5462756fb2e8fbd5fa"


@pytest.mark.parametrize(
    ("memory", "expected"),
    [(24 * 1024, "bf16"), (18 * 1024, "bf16"), (11 * 1024, "group-offload"), (8 * 1024, "nf4")],
)
def test_auto_profile_thresholds(memory: int, expected: str) -> None:
    assert select_profile("auto", memory) == expected


def test_auto_profile_rejects_too_small_gpu() -> None:
    with pytest.raises(RuntimeError, match="8 GiB"):
        select_profile("auto", 8191)


@pytest.mark.parametrize("profile", ["bf16", "group-offload", "nf4", "blockswap"])
def test_command_builder_passes_local_snapshots_without_shell(
    tmp_path: Path, profile: str
) -> None:
    settings = settings_for(tmp_path)
    record = {
        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "input_file": "input/random.png",
        "resolution": 1024,
        "seed": 123456789,
        "steps": 47,
        "depth_resolution": 1536,
    }
    command = build_inference_command(
        settings,
        record,
        profile,
        {"layerdiff": "/models/layer", "depth": "/models/depth"},
    )
    assert isinstance(command, list)
    assert "/models/layer" in command
    assert "/models/depth" in command
    assert "--save_to_psd" in command
    assert bundle_for_profile(profile) in {"bf16", "nf4"}
    assert "--disable_progressbar" in command
    assert command[command.index("--seed") + 1] == "123456789"
    assert command[command.index("--resolution_depth") + 1] == "1536"
    steps_flag = (
        "--inference_steps"
        if profile in {"bf16", "group-offload"}
        else "--num_inference_steps"
    )
    assert command[command.index(steps_flag) + 1] == "47"


def test_command_builder_defaults_controls_for_old_records(tmp_path: Path) -> None:
    command = build_inference_command(
        settings_for(tmp_path),
        {
            "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "input_file": "input/random.png",
            "resolution": 1280,
        },
        "bf16",
        {"layerdiff": "/models/layer", "depth": "/models/depth"},
    )
    assert command[command.index("--seed") + 1] == "42"
    assert command[command.index("--inference_steps") + 1] == "30"
    assert command[command.index("--resolution_depth") + 1] == "768"


def test_gpu_probe_reports_every_device_and_preserves_first_gpu_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "seethrough_server.model_cache.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=(
                "NVIDIA RTX 6000 Ada, 49140, 555.42\n"
                "NVIDIA B300, 286720, 555.42\n"
            )
        ),
    )

    result = GPUProbe().inspect()

    assert result["available"] is True
    assert result["count"] == 2
    assert result["name"] == "NVIDIA RTX 6000 Ada"
    assert result["memory_mib"] == 49140
    assert [device["name"] for device in result["devices"]] == [
        "NVIDIA RTX 6000 Ada",
        "NVIDIA B300",
    ]
    assert result["devices"][1]["index"] == 1
