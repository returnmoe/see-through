from __future__ import annotations

import asyncio
import ast
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
from seethrough_server.runner import JobManager, build_inference_command
from seethrough_server.storage import JobStore


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
    for bundle in lock["bundles"].values():
        required = bundle["repositories"]["layerdiff"]["required_files"]
        assert "model_index.json" in required
        assert "scheduler/scheduler_config.json" in required


@pytest.mark.asyncio
async def test_prefetch_uses_pinned_revisions_and_reuses_completed_paths(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    def download(repo_id: str, revision: str, cache: Path) -> str:
        calls.append((repo_id, revision))
        destination = cache / revision
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "model_index.json").write_text("{}\n", encoding="utf-8")
        if "layerdiff" in repo_id:
            scheduler = destination / "scheduler/scheduler_config.json"
            scheduler.parent.mkdir(parents=True, exist_ok=True)
            scheduler.write_text("{}\n", encoding="utf-8")
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

    layerdiff = Path(first["layerdiff"])
    (layerdiff / "scheduler/scheduler_config.json").unlink()
    third = await manager.ensure_bundle("nf4")
    assert third == first
    assert len(calls) == 4


def test_layerdiff_scheduler_has_no_hidden_repository_dependency() -> None:
    root = Path(__file__).parents[2]
    pipeline = (
        root / "common/modules/layerdiffuse/diffusers_kdiffusion_sdxl.py"
    ).read_text(encoding="utf-8")
    callers = [
        root / "common/utils/inference_utils.py",
        root / "inference/scripts/inference_psd_quantized.py",
        root / "inference/scripts/inference_psd_blockswap.py",
    ]

    assert "frankjoshua/juggernautXL_version6Rundiffusion" not in pipeline
    assert "load_layerdiff_scheduler" in pipeline
    for caller in callers:
        source = caller.read_text(encoding="utf-8")
        assert "load_layerdiff_scheduler" in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "from_pretrained":
                continue
            assert not any(
                keyword.arg == "scheduler"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is None
                for keyword in node.keywords
            )


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
        "tblr_split": True,
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
    assert "--tblr_split" in command
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
    assert "--tblr_split" not in command


@pytest.mark.asyncio
async def test_inference_output_is_teed_and_heartbeat_reports_quiet_work(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class DelayedStream:
        def __init__(self) -> None:
            self.calls = 0

        async def readline(self) -> bytes:
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(0.025)
                return b"\x1b[32mrunning layerdiff...\x1b[0m\n"
            return b""

    settings = settings_for(tmp_path)
    store = JobStore(settings.data_dir)
    staged = tmp_path / "source.png"
    staged.write_bytes(b"source")
    record = store.create(
        staged_input=staged,
        original_filename="source.png",
        extension=".png",
        profile="auto",
        resolution=1280,
    )
    store.update(record["id"], state="running", phase="starting", progress=10)
    manager = JobManager(
        settings,
        store,
        ModelManager(settings, downloader=lambda *_: ""),
        heartbeat_interval=0.005,
    )

    await manager._stream_process_output(
        record["id"],
        SimpleNamespace(stdout=DelayedStream()),
    )

    logs = store.get(record["id"])["logs"]
    assert any("Heartbeat: starting is still running" in line for line in logs)
    assert "running layerdiff..." in logs
    assert all("\x1b" not in line for line in logs)
    assert store.get(record["id"], raw=True)["phase"] == "layerdiff"

    output = capsys.readouterr().out
    assert f"[inference {record['id'][:8]}] Heartbeat:" in output
    assert f"[inference {record['id'][:8]}] running layerdiff..." in output
    assert "\x1b" not in output


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
