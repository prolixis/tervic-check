"""Every detector: at least one positive and one negative case."""

from __future__ import annotations

from typing import Any

import pytest
from helpers import findings, ids, node, record

import tervic
from tervic.analyze.detectors import REGISTRY
from tervic.core.run import Run

# --------------------------------------------------------------------------- 7.1 output integrity


def test_tv001_empty_output_when_other_calls_return_content() -> None:
    def body(run: Run) -> None:
        node(run, "summarize", {"t": "a"}, "summary one", mode=None)
        node(run, "summarize", {"t": "b"}, "", mode=None)

    report = record(body)
    assert "TV001" in ids(report)
    assert findings(report, "TV001")[0].severity == "error"


def test_tv001_warns_for_graph_step_writing_nothing() -> None:
    report = record(lambda run: node(run, "plan", {"q": "x"}, {}))
    assert findings(report, "TV001")[0].severity == "warn"


def test_tv001_negative_content_and_side_effect_none() -> None:
    def body(run: Run) -> None:
        node(run, "summarize", {"t": "a"}, "summary", mode=None)
        node(run, "log_it", {"t": "a"}, None, mode=None)  # side-effect step returning None

    assert "TV001" not in ids(record(body))


def test_tv002_dropped_field_read_later() -> None:
    def body(run: Run) -> None:
        node(run, "clean", {"q": "x", "user": "u1"}, {"q": "x"}, mode="replace")
        node(run, "answer", {"q": "x"}, {"q": "x", "a": "y"}, mode="replace", reads=["user"])

    report = record(body)
    finding = findings(report, "TV002")[0]
    assert finding.step_name == "clean" and finding.field_path == "user"


def test_tv002_negative_when_nobody_reads_it() -> None:
    def body(run: Run) -> None:
        node(run, "clean", {"q": "x", "tmp": 1}, {"q": "x"}, mode="replace")
        node(run, "answer", {"q": "x"}, {"q": "x", "a": "y"}, mode="replace", reads=["q"])

    assert "TV002" not in ids(record(body))


def test_tv003_null_field_read_later() -> None:
    def body(run: Run) -> None:
        node(run, "fetch", {"q": "x"}, {"profile": None})
        node(run, "greet", {"q": "x", "profile": None}, {"msg": "hi"}, reads=["profile"])

    assert findings(record(body), "TV003")[0].field_path == "profile"


def test_tv003_negative_value_present() -> None:
    def body(run: Run) -> None:
        node(run, "fetch", {"q": "x"}, {"profile": {"name": "Ada"}})
        node(run, "greet", {"q": "x", "profile": {"name": "Ada"}}, {"msg": "hi"}, reads=["profile"])

    assert "TV003" not in ids(record(body))


def test_tv004_never_written() -> None:
    def body(run: Run) -> None:
        node(run, "answer", {"q": "x"}, {"a": "y"}, reads=["context"])

    assert findings(record(body), "TV004")[0].field_path == "context"


def test_tv004_negative_written_earlier_or_in_input() -> None:
    def body(run: Run) -> None:
        node(run, "load", {"q": "x"}, {"context": "c"})
        node(run, "answer", {"q": "x", "context": "c"}, {"a": "y"}, reads=["context", "q"])

    assert "TV004" not in ids(record(body, input={"q": "x"}))


def test_tv005_type_drift_between_steps() -> None:
    def body(run: Run) -> None:
        node(run, "a", {}, {"items": ["x", "y"]})
        node(run, "b", {"items": ["x", "y"]}, {"items": "x,y"})

    finding = findings(record(body), "TV005")[0]
    assert finding.step_name == "b" and finding.evidence["new_type"] == "str"


def test_tv005_negative_same_type() -> None:
    def body(run: Run) -> None:
        node(run, "a", {}, {"items": ["x"]})
        node(run, "b", {"items": ["x"]}, {"items": ["x", "y"]})

    assert "TV005" not in ids(record(body))


def test_tv006_schema_violation_json_schema() -> None:
    tervic.contract("answer", schema={"type": "object", "required": ["answer", "sources"],
                                      "properties": {"answer": {"type": "string"}}})
    report = record(lambda run: node(run, "answer", {"q": "x"}, {"answer": 3}))
    messages = [f.message for f in findings(report, "TV006")]
    assert any("sources" in m for m in messages) and any("expected string" in m for m in messages)


def test_tv006_negative_schema_matches() -> None:
    tervic.contract("answer", schema={"type": "object", "required": ["answer"]})
    assert "TV006" not in ids(record(lambda run: node(run, "answer", {"q": "x"}, {"answer": "y"})))


def test_tv007_contract_violation() -> None:
    tervic.contract("retrieve", writes=["documents"], non_empty=["documents"], min_length={"documents": 2})
    report = record(lambda run: node(run, "retrieve", {"q": "x"}, {"documents": []}))
    assert len(findings(report, "TV007")) >= 2


def test_tv007_negative_contract_met() -> None:
    tervic.contract("retrieve", writes=["documents"], non_empty=["documents"], types={"documents": "list"})
    report = record(lambda run: node(run, "retrieve", {"q": "x"}, {"documents": ["a", "b"]}))
    assert "TV007" not in ids(report)


# --------------------------------------------------------------------------- 7.2 swallowed errors


@pytest.mark.parametrize("output", [
    {"error": "upstream timeout"},
    "Traceback (most recent call last):\n  File x\nValueError: bad",
    "Error: could not connect",
    "503 Service Unavailable",
    '{"status": 500, "detail": "boom"}',
    {"ok": False, "data": None},
])
def test_tv101_error_in_output(output: Any) -> None:
    report = record(lambda run: node(run, "call_api", {"q": "x"}, output, mode=None))
    assert "TV101" in ids(report)


@pytest.mark.parametrize("output", [{"error": None, "data": 1}, "All good: 200 OK", {"errors": []}])
def test_tv101_negative(output: Any) -> None:
    assert "TV101" not in ids(record(lambda run: node(run, "call_api", {"q": "x"}, output, mode=None)))


def test_tv102_tool_error_ignored() -> None:
    def body(run: Run) -> str:
        step = run.step_start("agent", "agent", {"q": "x"})
        run.tool_call("lookup", args={"id": 1}, result={"error": "timeout"}, step_id=step)
        run.llm_call(model="m", input_messages=["q"], output="It shipped!", step_id=step)
        run.step_end(step, "It shipped!")
        return "It shipped!"

    assert "TV102" in ids(record(body))


def test_tv102_negative_retry_succeeded() -> None:
    def body(run: Run) -> str:
        step = run.step_start("agent", "agent", {"q": "x"})
        run.tool_call("lookup", args={"id": 1}, result={"error": "timeout"}, step_id=step)
        run.tool_call("lookup", args={"id": 1}, result={"status": "shipped"}, step_id=step)
        run.step_end(step, "ok")
        return "ok"

    assert "TV102" not in ids(record(body))


def test_tv103_parse_failure_replaced_by_default() -> None:
    def body(run: Run) -> None:
        step = run.step_start("extract", "node", {"q": "x"})
        run.llm_call(model="m", input_messages=["give json"], output='{"name": "Ada", ', step_id=step)
        run.step_end(step, {"name": None, "age": None}, state_mode="update")

    assert "TV103" in ids(record(body))


def test_tv103_negative_valid_json() -> None:
    def body(run: Run) -> None:
        step = run.step_start("extract", "node", {"q": "x"})
        run.llm_call(model="m", input_messages=["give json"], output='{"name": "Ada"}', step_id=step)
        run.step_end(step, {"name": "Ada"}, state_mode="update")

    assert "TV103" not in ids(record(body))


def test_tv104_retries_exhausted_then_continue() -> None:
    def body(run: Run) -> None:
        for _ in range(3):
            node(run, "fetch", {"q": "x"}, None, mode=None, error=("TimeoutError", "slow"))
        node(run, "answer", {"q": "x"}, {"a": "fallback"})

    finding = findings(record(body), "TV104")[0]
    assert finding.evidence["attempts"] == 3


def test_tv104_negative_retry_recovered() -> None:
    def body(run: Run) -> None:
        node(run, "fetch", {"q": "x"}, None, mode=None, error=("TimeoutError", "slow"))
        node(run, "fetch", {"q": "x"}, {"data": 1})
        node(run, "answer", {"q": "x"}, {"a": "ok"})

    assert "TV104" not in ids(record(body))


# --------------------------------------------------------------------------- 7.3 LLM behaviour


def _llm(**kwargs: Any) -> Any:
    def body(run: Run) -> None:
        step = run.step_start("generate", "node", {"q": "x"})
        run.llm_call(step_id=step, **{"model": "gpt-4.1-mini", "input_messages": ["hi"], **kwargs})
        run.step_end(step, {"a": "done"}, state_mode="update")

    return record(body)


def test_tv201_truncated() -> None:
    assert "TV201" in ids(_llm(output="The answer is", finish_reason="length"))


def test_tv201_negative() -> None:
    assert "TV201" not in ids(_llm(output="The answer is 4.", finish_reason="stop"))


def test_tv202_refusal_field_and_text() -> None:
    assert findings(_llm(output="", refusal="I can't help with that."), "TV202")[0].severity == "error"
    text = findings(_llm(output="I'm sorry, but I can't help with that request."), "TV202")
    assert text and text[0].severity == "warn"


def test_tv202_negative() -> None:
    assert "TV202" not in ids(_llm(output="Sure, here is the summary you asked for."))


def test_tv203_hallucinated_tool() -> None:
    report = _llm(output="", tool_calls=[("delete_db", {})], available_tools=["search", "lookup"])
    assert findings(report, "TV203")[0].evidence["tool"] == "delete_db"


def test_tv203_negative() -> None:
    assert "TV203" not in ids(_llm(output="", tool_calls=[("search", {})], available_tools=["search"]))


def test_tv204_invalid_tool_args() -> None:
    assert "TV204" in ids(_llm(output="", tool_calls=[("search", '{"q": ')], available_tools=["search"]))

    def body(run: Run) -> None:
        run.tool_call("search", args={"q": 5}, result="r",
                      tool_schema={"type": "object", "required": ["q"], "properties": {"q": {"type": "string"}}})
        run.set_output("ok")

    assert "TV204" in ids(record(body))


def test_tv204_negative() -> None:
    def body(run: Run) -> None:
        run.tool_call("search", args={"q": "x"}, result="r",
                      tool_schema={"type": "object", "required": ["q"], "properties": {"q": {"type": "string"}}})
        run.set_output("ok")

    assert "TV204" not in ids(record(body))


def test_tv205_empty_completion() -> None:
    assert "TV205" in ids(_llm(output="  ", finish_reason="stop"))


def test_tv205_negative_tool_call_only() -> None:
    assert "TV205" not in ids(_llm(output="", tool_calls=[("search", {})], available_tools=["search"]))


# --------------------------------------------------------------------------- 7.4 retrieval


def test_tv301_and_tv302_empty_retrieval_then_answer() -> None:
    def body(run: Run) -> None:
        node(run, "retrieve", {"q": "x"}, {"documents": []}, kind="retriever")
        step = run.step_start("answer", "node", {"q": "x", "documents": []})
        run.llm_call(model="m", input_messages=["Context:\n\nQ: x"], output="I think...", step_id=step)
        run.step_end(step, {"answer": "I think..."}, state_mode="update")

    assert {"TV301", "TV302"} <= ids(record(body))


def test_tv302_docs_not_used() -> None:
    docs = [{"page_content": "Tervic records every step of an agent run."}]

    def body(run: Run) -> None:
        node(run, "retrieve", {"q": "x"}, {"documents": docs}, kind="retriever")
        step = run.step_start("answer", "node", {"q": "x"})
        run.llm_call(model="m", input_messages=["Answer from memory: x"], output="y", step_id=step)
        run.step_end(step, {"answer": "y"}, state_mode="update")

    report = record(body)
    assert "TV302" in ids(report) and "TV301" not in ids(report)


def test_tv301_tv302_negative_docs_used() -> None:
    docs = [{"page_content": "Tervic records every step of an agent run."}]

    def body(run: Run) -> None:
        node(run, "retrieve", {"q": "x"}, {"documents": docs}, kind="retriever")
        step = run.step_start("answer", "node", {"q": "x"})
        run.llm_call(model="m", input_messages=[f"Context: {docs[0]['page_content']}\nQ: x"], output="y",
                     step_id=step)
        run.step_end(step, {"answer": "y"}, state_mode="update")

    report = record(body)
    assert not ({"TV301", "TV302"} & ids(report))


# --------------------------------------------------------------------------- 7.5 control flow & budgets


def test_tv401_step_loop_and_tool_loop() -> None:
    def body(run: Run) -> str:
        for _ in range(6):
            node(run, "think", {"q": "x"}, {"t": "again"})
            run.tool_call("search", args={"q": "same"}, result="nothing")
        return "done"

    report = record(body)
    assert len(findings(report, "TV401")) == 2


def test_tv401_negative_different_inputs() -> None:
    def body(run: Run) -> str:
        for i in range(8):
            node(run, "think", {"q": i}, {"t": i})
            run.tool_call("search", args={"q": i}, result="r")
        return "done"

    assert "TV401" not in ids(record(body))


def test_tv401_threshold_configurable() -> None:
    tervic.configure(detectors={"loop_threshold": 2})

    def body(run: Run) -> str:
        for _ in range(3):
            run.tool_call("search", args={"q": "same"}, result="r")
        return "done"

    assert "TV401" in ids(record(body))


def test_tv402_dead_end() -> None:
    def body(run: Run) -> None:
        node(run, "final", {"q": "x"}, None, mode=None)

    assert "TV402" in ids(record(body))


def test_tv402_negative() -> None:
    assert "TV402" not in ids(record(lambda run: "an answer"))


def test_tv403_tv404_tv405_budgets() -> None:
    tervic.configure(budget={"cost_usd": 0.001, "latency_ms": 0.0001, "tokens": 10})

    def body(run: Run) -> str:
        run.llm_call(model="gpt-4o", input_messages=["x"], output="y", input_tokens=5000, output_tokens=500)
        return "y"

    report = record(body)
    assert {"TV403", "TV404", "TV405"} <= ids(report)


def test_budgets_negative_when_unset_or_within() -> None:
    tervic.configure(budget={"cost_usd": 10.0, "tokens": 1_000_000})

    def body(run: Run) -> str:
        run.llm_call(model="gpt-4o", input_messages=["x"], output="y", input_tokens=50, output_tokens=5)
        return "y"

    assert not ({"TV403", "TV404", "TV405"} & ids(record(body)))


# --------------------------------------------------------------------------- 7.6 safety


def test_tv501_secret_sent_to_llm() -> None:
    def body(run: Run) -> str:
        run.llm_call(model="m", input_messages=["use key sk-proj-abcdefghijklmnopqrstuvwxyz123456"], output="ok")
        return "ok"

    report = record(body)
    assert "api_key" in findings(report, "TV501")[0].evidence["kinds"]


def test_tv501_secret_in_final_output_without_redaction() -> None:
    tervic.configure(redact=False)
    report = record(lambda run: "here: AKIAABCDEFGHIJKLMNOP")
    assert "TV501" in ids(report)


def test_tv501_negative() -> None:
    def body(run: Run) -> str:
        run.llm_call(model="m", input_messages=["use YOUR_API_KEY from the env"], output="ok")
        return "ok"

    assert "TV501" not in ids(record(body))


def test_tv502_pii_in_output() -> None:
    report = record(lambda run: "Contact ada@example.org or +1 415 555 0100")
    assert findings(report, "TV502")[0].severity == "warn"


def test_tv502_negative() -> None:
    assert "TV502" not in ids(record(lambda run: "No personal data here, order 42 shipped."))


# --------------------------------------------------------------------------- configuration of detectors


def test_detectors_can_be_disabled_globally_and_per_step() -> None:
    tervic.configure(detectors={"disable": ["TV301"], "per_step": {"retrieve": {"disable": ["TV003"]}}})

    def body(run: Run) -> None:
        node(run, "retrieve", {"q": "x"}, {"documents": []}, kind="retriever")
        node(run, "answer", {"q": "x", "documents": []}, {"a": "y"}, reads=["documents"])

    report = record(body)
    assert not ({"TV301", "TV003"} & ids(report))


def test_severity_override_changes_verdict() -> None:
    tervic.configure(detectors={"severity": {"TV301": "info"}})
    report = record(lambda run: node(run, "retrieve", {"q": "x"}, [], kind="retriever", mode=None) and "x")
    assert findings(report, "TV301")[0].severity == "info"


def test_every_detector_is_documented() -> None:
    from pathlib import Path

    doc = (Path(__file__).parents[2] / "docs" / "DETECTORS.md").read_text(encoding="utf-8")
    for cls in REGISTRY:
        assert cls.id in doc and cls.name in doc, cls.id
        assert cls.description and cls.how_to_fix, cls.id
