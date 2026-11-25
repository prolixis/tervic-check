"""broken.py must fail with TV401; fixed.py must pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("crewai")

from _helpers import check_cli, load_example  # noqa: E402

HERE = Path(__file__).parent


def test_broken_fails_with_loop(store: Path) -> None:
    load_example(HERE / "broken.py").run()
    code, output = check_cli("check", "--json")
    assert code == 1, output
    report = json.loads(output)["runs"][0]
    assert "TV401" in {f["detector_id"] for f in report["findings"]}
    loop_group = next(g for g in report["groups"] if "TV401" in g["detector_ids"])
    assert loop_group["root_cause_step_name"] == "research"


def test_fixed_passes(store: Path) -> None:
    answer = load_example(HERE / "fixed.py").run()
    assert "Tervic" in answer
    code, output = check_cli("check")
    assert code == 0, output
