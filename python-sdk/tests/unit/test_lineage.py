"""Root cause on synthetic graphs: walk back to the earliest step where the value became bad."""

from __future__ import annotations

from pathlib import Path

from helpers import node, record

from tervic.core.run import Run


def test_root_cause_walks_back_through_a_chain() -> None:
    def body(run: Run) -> None:
        node(run, "parse", {"raw": "x"}, {"query": ""})                         # bad value born here
        node(run, "rewrite", {"raw": "x", "query": ""}, {"query2": ""}, reads=["query"])
        node(run, "retrieve", {"query2": ""}, {"documents": []}, kind="retriever", reads=["query2"])
        node(run, "answer", {"documents": []}, {"answer": "idk"}, reads=["documents"])

    report = record(body)
    roots = {g.root_cause_step_name for g in report.groups}
    assert roots == {"parse"}, [(g.root_cause_step_name, g.detector_ids) for g in report.groups]
    group = report.groups[0]
    assert {"TV003", "TV301"} <= set(group.detector_ids)
    assert group.findings[0].step_name == "parse"


def test_independent_problems_stay_separate_groups() -> None:
    def body(run: Run) -> None:
        node(run, "retrieve", {"q": "x"}, {"documents": []}, kind="retriever")
        node(run, "lookup", {"q": "x"}, {"error": "timeout"}, mode=None)
        node(run, "answer", {"q": "x", "documents": []}, {"answer": "ok"}, reads=["documents"])

    report = record(body)
    roots = sorted(g.root_cause_step_name or "" for g in report.groups)
    assert roots == ["lookup", "retrieve"]


def test_root_cause_skips_good_upstream_values() -> None:
    def body(run: Run) -> None:
        node(run, "parse", {"raw": "x"}, {"query": "tervic"})
        node(run, "retrieve", {"query": "tervic"}, {"documents": []}, kind="retriever", reads=["query"])

    report = record(body)
    assert report.groups[0].root_cause_step_name == "retrieve"


def test_specific_finding_heads_the_group() -> None:
    def body(run: Run) -> None:
        node(run, "retrieve", {"q": "x"}, {"documents": []}, kind="retriever")
        node(run, "answer", {"q": "x", "documents": []}, {"a": "y"}, reads=["documents"])

    group = record(body).groups[0]
    assert group.findings[0].detector_id == "TV301"  # more specific than TV003


def test_findings_carry_source_location() -> None:
    import tervic

    @tervic.step(kind="retriever")
    def retrieve(state: dict) -> dict:  # type: ignore[type-arg]
        return {**state, "documents": []}

    with tervic.run("loc") as run:
        retrieve({"q": "x"})
    run.wait()
    assert run.report is not None
    source = run.report.groups[0].source
    lines = Path(__file__).read_text(encoding="utf-8").splitlines()
    def_line = next(i for i, text in enumerate(lines, 1) if text.strip().startswith("def retrieve"))
    assert source is not None and source.endswith(f"test_lineage.py:{def_line}")
