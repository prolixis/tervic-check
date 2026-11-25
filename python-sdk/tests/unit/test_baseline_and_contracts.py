from __future__ import annotations

from pathlib import Path

from helpers import ids, node, record
from pydantic import BaseModel

import tervic
from tervic.analyze.baseline import diff_runs
from tervic.analyze.contracts import check_step, infer, to_toml, validate_schema
from tervic.analyze.model import build_view
from tervic.api import store_for
from tervic.config import Contract
from tervic.core.run import Run


def _happy(run: Run) -> str:
    node(run, "retrieve", {"q": "x"}, {"documents": ["d1", "d2", "d3"]}, kind="retriever")
    node(run, "answer", {"q": "x", "documents": ["d1"]}, {"answer": "good"}, reads=["documents"])
    return "good"


def _regressed(run: Run) -> str:
    node(run, "retrieve", {"q": "x"}, {"documents": []}, kind="retriever")
    node(run, "rerank", {"q": "x"}, {"documents": []})
    return "meh"


def test_diff_runs_structure_sequence_and_metrics() -> None:
    a = record(_happy, name="rag")
    b = record(_regressed, name="rag")
    store = store_for()
    view_a = build_view(store.load_events(a.run_id))
    view_b = build_view(store.load_events(b.run_id))
    diff = diff_runs(view_a, view_b)
    assert diff.steps_removed == ["answer"] and diff.steps_added == ["rerank"]
    changes = {(c.step, c.field, c.change) for c in diff.changes}
    assert ("retrieve", "documents", "became_empty") in changes
    assert not diff.is_empty
    same = diff_runs(view_a, view_a)
    assert same.is_empty


def test_baseline_is_used_automatically_by_run_name(project: Path) -> None:
    good = record(_happy, name="rag")
    store = store_for()
    store.save_baseline("rag", good.run_id)
    assert store.list_baselines()[0]["name"] == "rag"
    assert (store.baselines_dir / "rag" / "events.jsonl").is_file()
    bad = record(_regressed, name="rag")
    assert bad.baseline == "rag"
    assert "TV701" in ids(bad)
    assert bad.baseline_diff is not None and bad.baseline_diff["steps_removed"] == ["answer"]
    assert store.remove_baseline("rag") and not store.remove_baseline("rag")


def test_tv001_uses_baseline_evidence() -> None:
    good = record(lambda run: node(run, "summarize", {"t": "x"}, "a summary", mode=None) and "ok", name="sum")
    store_for().save_baseline("sum", good.run_id)
    bad = record(lambda run: node(run, "summarize", {"t": "x"}, None, mode=None) and "ok", name="sum")
    finding = next(f for f in bad.findings if f.detector_id == "TV001")
    assert "baseline" in finding.message


class Answer(BaseModel):
    answer: str
    sources: list[str]


def test_check_step_contract_rules() -> None:
    def body(run: Run) -> None:
        node(run, "answer", {"documents": []}, {"answer": "short"})

    report = record(body)
    view = build_view(store_for().load_events(report.run_id))
    step = view.steps[0]
    spec = Contract(step="answer", reads=["documents", "question"], writes=["answer", "sources"],
                    non_empty=["answer"], min_length={"answer": 20}, max_length={"answer": 2},
                    types={"answer": "list"}, schema=f"{__name__}:Answer")
    messages = [v.message for v in check_step(step, spec)]
    assert any("must read `question`" in m for m in messages)
    assert any("`documents`" in m and "empty" in m for m in messages)
    assert any("must write `sources`" in m for m in messages)
    assert any("length ≥ 20" in m for m in messages)
    assert any("length ≤ 2" in m for m in messages)
    assert any("type list" in m for m in messages)
    assert any("schema" in m and "sources" in m for m in messages)


def test_validate_schema_variants(project: Path) -> None:
    assert validate_schema(f"{__name__}:Answer", {"answer": "a", "sources": []}) == []
    assert validate_schema(f"{__name__}.Answer", {"answer": "a"})
    assert "could not load" in validate_schema("nope.module:Thing", {})[0][1]
    schema_file = project / "s.json"
    schema_file.write_text('{"type": "object", "required": ["a"]}', encoding="utf-8")
    assert validate_schema(str(schema_file), {"b": 1})[0][0] == "a"
    assert validate_schema({"type": "array", "minItems": 2, "items": {"type": "integer"}}, [1.5])
    assert validate_schema({"type": "string", "enum": ["x"], "minLength": 3}, "y")


def test_infer_contracts_from_runs() -> None:
    reports = [record(_happy, name="rag") for _ in range(3)]
    store = store_for()
    views = [build_view(store.load_events(r.run_id)) for r in reports]
    contracts = infer(views)
    assert contracts["retrieve"].writes == ["documents"]
    assert contracts["retrieve"].non_empty == ["documents"]
    assert contracts["retrieve"].types == {"documents": "list"}
    assert contracts["answer"].reads == ["documents"]
    text = to_toml(contracts)
    assert "[contracts.retrieve]" in text and 'writes = ["documents"]' in text


def test_contract_from_code_with_model_class() -> None:
    spec = tervic.contract("answer", schema=Answer, writes=["answer"])
    assert spec.schema_ == f"{__name__}:Answer"
    report = record(lambda run: node(run, "answer", {"q": "x"}, {"answer": "y"}) and "y")
    assert "TV006" in ids(report)
