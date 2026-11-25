"""Fixtures for the example tests: an isolated Tervic store per test."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from tervic.config import reset_config

    monkeypatch.chdir(tmp_path)
    for var in list(os.environ):
        if var.startswith("TERVIC_"):
            monkeypatch.delenv(var)
    reset_config()
    yield tmp_path / ".tervic"
    reset_config()
