from __future__ import annotations

from pathlib import Path

import pytest

import tervic
from tervic.config import build_config, find_config_file, get_config, reset_config

TOML = """
[tervic]
mode = "ci"
fail_on = "warn"
retention_runs = 50

[budget]
cost_usd = 0.25

[detectors]
disable = ["TV502"]
loop_threshold = 3

[detectors.per_step.summarize]
disable = ["TV001"]

[contracts.retrieve]
writes = ["documents"]
non_empty = ["documents"]

[replay]
entrypoint = "app.main:run"
"""


def test_defaults(project: Path) -> None:
    config = get_config()
    assert config.mode == "dev" and config.fail_on == "error" and config.retention_runs == 200
    assert config.store_path() == Path(".") / ".tervic"
    assert find_config_file() is None


def test_toml_is_loaded(project: Path) -> None:
    (project / "tervic.toml").write_text(TOML, encoding="utf-8")
    reset_config()
    config = get_config()
    assert config.mode == "ci" and config.fail_on == "warn" and config.retention_runs == 50
    assert config.budget.cost_usd == 0.25 and config.detectors.loop_threshold == 3
    assert config.contracts["retrieve"].writes == ["documents"]
    assert config.replay.entrypoint == "app.main:run"
    assert not config.detector_enabled("TV502")
    assert not config.detector_enabled("TV001", "summarize") and config.detector_enabled("TV001", "other")


def test_precedence_code_over_env_over_toml_over_defaults(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "tervic.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.setenv("TERVIC_MODE", "prod")
    monkeypatch.setenv("TERVIC_RETENTION_RUNS", "7")
    reset_config()
    config = get_config()
    assert config.mode == "prod"  # env beats toml
    assert config.retention_runs == 7
    assert config.fail_on == "warn"  # toml beats default
    tervic.configure(mode="dev", fail_on="error")
    config = get_config()
    assert config.mode == "dev" and config.fail_on == "error"  # code beats env
    assert config.retention_runs == 7


def test_env_booleans_and_explicit_config_path(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = project / "elsewhere.toml"
    other.write_text('[tervic]\nmode = "ci"\n', encoding="utf-8")
    monkeypatch.setenv("TERVIC_CONFIG", str(other))
    monkeypatch.setenv("TERVIC_REDACT", "false")
    monkeypatch.setenv("TERVIC_REDACT_PII", "yes")
    reset_config()
    config = get_config()
    assert config.mode == "ci" and config.redact is False and config.redact_pii is True


def test_nested_configure_merges(project: Path) -> None:
    tervic.configure(budget={"cost_usd": 1.0})
    tervic.configure(budget={"tokens": 100})
    config = get_config()
    assert config.budget.cost_usd == 1.0 and config.budget.tokens == 100


def test_invalid_values_raise(project: Path) -> None:
    with pytest.raises(ValueError):
        build_config({"sample_rate": 2.0})
    with pytest.raises(ValueError):
        build_config({"mode": "staging"})


def test_contract_from_code_survives_reconfigure(project: Path) -> None:
    tervic.contract("answer", writes=["answer"])
    tervic.configure(mode="ci")
    assert get_config().contracts["answer"].writes == ["answer"]
