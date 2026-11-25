"""Configuration: ``configure()`` > environment variables > ``tervic.toml`` > defaults."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

Mode = Literal["dev", "ci", "prod"]
Severity = Literal["error", "warn", "info"]
FailOn = Literal["error", "warn"]
Provider = Literal["openai", "anthropic", "gemini"]

CONFIG_FILE = "tervic.toml"


class Contract(BaseModel):
    """What a step must read and produce. Violations are error-severity findings."""

    step: str = ""
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    non_empty: list[str] = Field(default_factory=list)
    schema_: str | dict[str, Any] | None = Field(default=None, alias="schema")
    min_length: dict[str, int] = Field(default_factory=dict)
    max_length: dict[str, int] = Field(default_factory=dict)
    types: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cost_usd: float | None = None
    latency_ms: float | None = None
    step_latency_ms: float | None = None
    tokens: int | None = None


class StepDetectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disable: list[str] = Field(default_factory=list)


class DetectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disable: list[str] = Field(default_factory=list)
    loop_threshold: int = 5
    per_step: dict[str, StepDetectorConfig] = Field(default_factory=dict)
    severity: dict[str, Severity] = Field(default_factory=dict)
    baseline_regression_pct: float = 50.0


class JudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: Provider = "anthropic"
    model: str | None = None
    max_calls: int = 10
    max_cost_usd: float = 0.05


class ReplayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrypoint: str | None = None


class WebhookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    format: Literal["json", "slack"] = "json"
    on: Literal["fail", "warn", "always"] = "fail"
    timeout_s: float = 5.0


class OtelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    service_name: str = "tervic"


class TervicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mode: Mode = "dev"
    enabled: bool = True
    store_dir: str = ".tervic"
    fail_on: FailOn = "error"
    retention_runs: int = 200
    sample_rate: float = 1.0
    redact: bool = True
    redact_pii: bool = False
    redact_fn: Callable[[Any], Any] | None = Field(default=None, exclude=True)
    capture_inputs: bool = True
    record_cassette: bool | None = None
    max_events_per_run: int = 10_000
    budget: Budget = Field(default_factory=Budget)
    detectors: DetectorConfig = Field(default_factory=DetectorConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    replay: ReplayConfig = Field(default_factory=ReplayConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    otel: OtelConfig = Field(default_factory=OtelConfig)
    contracts: dict[str, Contract] = Field(default_factory=dict)
    root: str = "."

    @field_validator("sample_rate")
    @classmethod
    def _rate(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("sample_rate must be between 0 and 1")
        return value

    def store_path(self) -> Path:
        path = Path(self.store_dir)
        return path if path.is_absolute() else Path(self.root) / path

    def detector_enabled(self, detector_id: str, step_name: str | None = None) -> bool:
        if detector_id in self.detectors.disable:
            return False
        if step_name is not None:
            per = self.detectors.per_step.get(step_name)
            if per is not None and detector_id in per.disable:
                return False
        return True


_ENV: dict[str, tuple[str, Callable[[str], Any]]] = {
    "TERVIC_MODE": ("mode", str),
    "TERVIC_STORE_DIR": ("store_dir", str),
    "TERVIC_FAIL_ON": ("fail_on", str),
    "TERVIC_SAMPLE_RATE": ("sample_rate", float),
    "TERVIC_RETENTION_RUNS": ("retention_runs", int),
    "TERVIC_REDACT": ("redact", lambda v: v.strip().lower() not in ("0", "false", "no", "off")),
    "TERVIC_REDACT_PII": ("redact_pii", lambda v: v.strip().lower() in ("1", "true", "yes", "on")),
    "TERVIC_ENABLED": ("enabled", lambda v: v.strip().lower() not in ("0", "false", "no", "off")),
    "TERVIC_WEBHOOK_URL": ("webhook.url", str),
    "TERVIC_REPLAY_ENTRYPOINT": ("replay.entrypoint", str),
}


def find_config_file(start: Path | None = None) -> Path | None:
    explicit = os.environ.get("TERVIC_CONFIG")
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    current = (start or Path.cwd()).resolve()
    for folder in (current, *current.parents):
        candidate = folder / CONFIG_FILE
        if candidate.is_file():
            return candidate
        if (folder / ".git").exists():
            break
    return None


def _set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    data: dict[str, Any] = dict(raw.get("tervic", {}))
    for section in ("budget", "detectors", "judge", "replay", "webhook", "otel"):
        if section in raw:
            data[section] = raw[section]
    if "contracts" in raw:
        data["contracts"] = {name: {**spec, "step": name} for name, spec in raw["contracts"].items()}
    data.setdefault("root", str(path.parent))
    return data


def build_config(overrides: dict[str, Any] | None = None, start: Path | None = None) -> TervicConfig:
    data: dict[str, Any] = {}
    config_file = find_config_file(start)
    if config_file is not None:
        data = load_toml(config_file)
    env: dict[str, Any] = {}
    for var, (dotted, cast) in _ENV.items():
        raw = os.environ.get(var)
        if raw is not None and raw != "":
            _set_path(env, dotted, cast(raw))
    data = _deep_merge(data, env)
    if overrides:
        nested: dict[str, Any] = {}
        for key, value in overrides.items():
            _set_path(nested, key.replace("__", "."), value)
        data = _deep_merge(data, nested)
    return TervicConfig.model_validate(data)


_lock = threading.RLock()
_config: TervicConfig | None = None
_overrides: dict[str, Any] = {}
_contracts_from_code: dict[str, Contract] = {}


def get_config() -> TervicConfig:
    global _config
    with _lock:
        if _config is None:
            _config = build_config(_overrides)
            for name, contract in _contracts_from_code.items():
                _config.contracts[name] = contract
        return _config


def configure(**kwargs: Any) -> TervicConfig:
    """Set configuration from code. Highest precedence. Nested keys: ``budget={"cost_usd": 1}``."""
    global _config
    with _lock:
        for key, value in kwargs.items():
            if isinstance(value, dict) and isinstance(_overrides.get(key), dict):
                _overrides[key] = _deep_merge(_overrides[key], value)
            else:
                _overrides[key] = value
        _config = None
        return get_config()


def add_contract(contract: Contract) -> None:
    global _config
    with _lock:
        _contracts_from_code[contract.step] = contract
        _config = None


def reset_config() -> None:
    """Forget code overrides and re-read env/TOML on next use (used by tests)."""
    global _config
    with _lock:
        _config = None
        _overrides.clear()
        _contracts_from_code.clear()
