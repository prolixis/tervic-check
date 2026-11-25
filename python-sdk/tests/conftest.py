"""Every test runs in its own temporary project folder with a fresh Tervic configuration."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from tervic.config import reset_config
    from tervic.core.run import set_replay_state
    from tervic.core.safety import reset_internal_errors

    monkeypatch.chdir(tmp_path)
    for var in list(os.environ):
        if var.startswith("TERVIC_"):
            monkeypatch.delenv(var)
    reset_config()
    reset_internal_errors()
    set_replay_state(None)
    yield tmp_path
    set_replay_state(None)
    reset_config()


@pytest.fixture
def store(project: Path) -> Path:
    return project / ".tervic"
