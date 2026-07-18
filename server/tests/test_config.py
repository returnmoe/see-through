from __future__ import annotations

from pathlib import Path

import pytest

from seethrough_server.config import Settings


def test_defaults_are_loopback_first(tmp_path: Path) -> None:
    settings = Settings.from_env({}, repo_root=tmp_path)
    assert settings.bind_host == "127.0.0.1"
    assert settings.port == 4321
    assert settings.loopback_only is True
    assert settings.data_dir == Path("/home/seethrough/data")
    assert settings.model_cache == Path("/home/seethrough/.cache/huggingface")


def test_explicit_listener_and_storage_overrides(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "SEE_THROUGH_BIND_HOST": "0.0.0.0",
            "SEE_THROUGH_PORT": "8080",
            "SEE_THROUGH_DATA_DIR": str(tmp_path / "data"),
            "SEE_THROUGH_MODEL_CACHE": str(tmp_path / "models"),
            "SEE_THROUGH_PREFETCH": "nf4",
        },
        repo_root=tmp_path,
    )
    assert settings.bind_host == "0.0.0.0"
    assert settings.health_host == "127.0.0.1"
    assert settings.loopback_only is False
    assert settings.port == 8080
    assert settings.prefetch == "nf4"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SEE_THROUGH_BIND_HOST", "localhost"),
        ("SEE_THROUGH_BIND_HOST", "example.com"),
        ("SEE_THROUGH_PORT", "22"),
        ("SEE_THROUGH_PORT", "80"),
        ("SEE_THROUGH_PORT", "not-a-port"),
        ("SEE_THROUGH_PREFETCH", "everything"),
    ],
)
def test_invalid_environment_fails_closed(name: str, value: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Settings.from_env({name: value}, repo_root=tmp_path)


def test_hf_home_is_secondary_cache_override(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {"HF_HOME": str(tmp_path / "hf")}, repo_root=tmp_path
    )
    assert settings.model_cache == tmp_path / "hf"
