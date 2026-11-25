"""broken.py must fail with TV002 rooted at `normalize`; fixed.py must pass."""

from __future__ import annotations

import json
from pathlib import Path

from _helpers import check_cli, load_example

HERE = Path(__file__).parent


def test_broken_fails_with_root_cause(store: Path) -> None:
    reply = load_example(HERE / "broken.py").run()
    assert "Hi there" in reply  # the silent failure: no exception, wrong answer
    code, output = check_cli("check", "--json")
    assert code == 1, output
    report = json.loads(output)["runs"][0]
    assert "TV002" in {f["detector_id"] for f in report["findings"]}
    group = report["groups"][0]
    assert group["root_cause_step_name"] == "normalize"
    lines = (HERE / "broken.py").read_text(encoding="utf-8").splitlines()
    def_line = next(i for i, text in enumerate(lines, 1) if text.startswith("def normalize"))
    assert group["source"].endswith(f"broken.py:{def_line}")


def test_fixed_passes(store: Path) -> None:
    reply = load_example(HERE / "fixed.py").run()
    assert "Hi Ada" in reply
    code, output = check_cli("check")
    assert code == 0, output


def test_fix_prompt_points_at_root_cause(store: Path) -> None:
    load_example(HERE / "broken.py").run()
    code, output = check_cli("fix", "--no-copy")
    assert code == 0
    assert "Root cause: step `normalize`" in output
    assert "tervic replay" in output and "--from normalize" in output
