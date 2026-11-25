"""broken.py must fail `tervic check` with TV301/TV302 rooted at `retrieve`; fixed.py must pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from _helpers import check_cli, load_example  # noqa: E402

HERE = Path(__file__).parent


def test_broken_fails_with_root_cause(store: Path) -> None:
    load_example(HERE / "broken.py").run()
    code, output = check_cli("check", "--json")
    assert code == 1, output
    report = json.loads(output)["runs"][0]
    ids = {f["detector_id"] for f in report["findings"]}
    assert {"TV301", "TV302"} <= ids
    assert report["groups"][0]["root_cause_step_name"] == "retrieve"


def test_fixed_passes(store: Path) -> None:
    result = load_example(HERE / "fixed.py").run()
    assert "Tervic" in result["answer"]
    code, output = check_cli("check")
    assert code == 0, output
