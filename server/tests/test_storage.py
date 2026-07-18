from __future__ import annotations

from pathlib import Path

import pytest

from seethrough_server.storage import JobStore


def _create(store: JobStore, tmp_path: Path) -> dict:
    staged = tmp_path / "staged"
    staged.write_bytes(b"image")
    return store.create(
        staged_input=staged,
        original_filename="portrait.png",
        extension=".png",
        profile="auto",
        resolution=1280,
    )


def test_running_job_becomes_interrupted_on_restart(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "data")
    store.initialize()
    job = _create(store, tmp_path)
    store.update(job["id"], state="running", phase="layerdiff")

    JobStore(tmp_path / "data").initialize()
    recovered = store.get(job["id"])
    assert recovered["state"] == "interrupted"
    assert recovered["status"] == "interrupted"
    assert recovered["finished_at"] is not None


@pytest.mark.parametrize("state", ["queued", "waiting_for_models"])
def test_recoverable_job_returns_to_queue(tmp_path: Path, state: str) -> None:
    store = JobStore(tmp_path / "data")
    store.initialize()
    job = _create(store, tmp_path)
    store.update(job["id"], state=state)
    recovered = JobStore(tmp_path / "data").initialize()
    assert [item["id"] for item in recovered] == [job["id"]]
    assert store.get(job["id"])["state"] == "queued"


def test_view_normalizes_ui_fields_and_hides_artifact_path(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "data")
    store.initialize()
    job = _create(store, tmp_path)
    artifact = store.job_dir(job["id"]) / "result.psd"
    artifact.write_bytes(b"psd")
    store.update(
        job["id"],
        artifacts=[
            {
                "id": "artifact",
                "name": "result.psd",
                "path": "result.psd",
                "kind": "psd",
                "size_bytes": 3,
                "content_type": "image/vnd.adobe.photoshop",
            }
        ],
    )
    view = store.get(job["id"])
    assert view["input_name"] == "portrait.png"
    assert view["config"]["resolution"] == 1280
    assert view["config"]["seed"] == 42
    assert view["config"]["steps"] == 30
    assert view["config"]["depth_resolution"] == 768
    assert view["config"]["tblr_split"] is False
    assert view["seed"] == 42
    assert view["steps"] == 30
    assert view["depth_resolution"] == 768
    assert view["tblr_split"] is False
    assert "path" not in view["artifacts"][0]
    assert view["artifacts"][0]["url"].endswith("/artifacts/artifact")


def test_view_defaults_new_config_fields_for_old_records(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "data")
    store.initialize()
    job = _create(store, tmp_path)
    record = store.get(job["id"], raw=True)
    del record["seed"]
    del record["steps"]
    del record["depth_resolution"]
    del record["tblr_split"]

    view = store.view(record)

    assert view["config"]["seed"] == 42
    assert view["config"]["steps"] == 30
    assert view["config"]["depth_resolution"] == 768
    assert view["config"]["tblr_split"] is False
    assert view["seed"] == 42
    assert view["steps"] == 30
    assert view["depth_resolution"] == 768
    assert view["tblr_split"] is False


def test_invalid_job_identifier_cannot_escape_data_directory(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "data")
    store.initialize()
    with pytest.raises(KeyError):
        store.get("../../outside")
