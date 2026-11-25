"""broken.py must fail with TV101/TV102 rooted at `lookup_order`; fixed.py must pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("agents")

from _helpers import check_cli, load_example  # noqa: E402

HERE = Path(__file__).parent


def test_broken_fails_with_root_cause(store: Path) -> None:
    load_example(HERE / "broken.py").run()
    code, output = check_cli("check", "--json")
    assert code == 1, output
    report = json.loads(output)["runs"][0]
    ids = {f["detector_id"] for f in report["findings"]}
    assert {"TV101", "TV102"} <= ids
    assert report["groups"][0]["root_cause_step_name"] == "lookup_order"


def test_fixed_passes(store: Path) -> None:
    answer = load_example(HERE / "fixed.py").run()
    assert "shipped" in answer
    code, output = check_cli("check")
    assert code == 0, output
