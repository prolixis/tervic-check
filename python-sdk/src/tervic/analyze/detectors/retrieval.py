"""7.4 Retrieval / RAG: TV301–TV302."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from tervic.analyze.detectors._util import enabled, output_text
from tervic.analyze.findings import Context, Detector, Finding, register
from tervic.analyze.model import StepView
from tervic.core.serialize import MARK, is_empty, item_count

_DOC_KEYS = ("documents", "docs", "results", "chunks", "passages", "contexts", "context", "sources", "hits")
_TEXT_KEYS = ("page_content", "content", "text", "body", "snippet", "chunk")


def is_retriever(step: StepView) -> bool:
    return step.kind == "retriever"


def documents_of(output: Any) -> list[Any] | None:
    """The list of documents a retriever produced, if it can be found."""
    if isinstance(output, list):
        return output
    if isinstance(output, dict) and MARK not in output:
        for key in _DOC_KEYS:
            if key in output and isinstance(output[key], list):
                return list(output[key])
        lists = [v for v in output.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    return None


def doc_text(doc: Any) -> str:
    if isinstance(doc, dict) and MARK not in doc:
        for key in _TEXT_KEYS:
            if key in doc and not is_empty(doc[key]):
                return output_text(doc[key])
    return output_text(doc)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


@register
class EmptyRetrieval(Detector):
    id = "TV301"
    name = "empty-retrieval"
    severity = "error"
    description = "A retriever returned 0 documents."
    how_to_fix = "Check the query, the index and any filters. If no results is valid, return an explicit 'no results' state the answer step handles."

    def run(self, ctx: Context) -> Iterator[Finding]:
        for step in ctx.run.steps:
            if not is_retriever(step) or step.error is not None or not step.ended:
                continue
            if not enabled(ctx, self.id, step):
                continue
            docs = documents_of(step.output)
            empty = is_empty(step.output) or (docs is not None and (item_count(docs) or 0) == 0)
            if empty:
                yield self.finding(
                    f"Retriever `{step.name}` returned 0 documents.",
                    step=step,
                    field_path=_doc_field(step.output),
                    evidence={"input": step.input, "output": step.output},
                )


def _doc_field(output: Any) -> str | None:
    if isinstance(output, dict) and MARK not in output:
        for key in _DOC_KEYS:
            if key in output:
                return key
    return None


@register
class RetrievalUnused(Detector):
    id = "TV302"
    name = "retrieval-unused"
    severity = "warn"
    description = (
        "None of the retrieved documents appear in any later LLM input, so the answer was produced "
        "without them (this includes answering after an empty retrieval)."
    )
    how_to_fix = "Pass the retrieved documents into the answer prompt, and do not answer when retrieval returned nothing."

    def run(self, ctx: Context) -> Iterator[Finding]:
        for step in ctx.run.steps:
            if not is_retriever(step) or step.error is not None or not enabled(ctx, self.id, step):
                continue
            later_calls = [c for c in ctx.run.llm_calls if c.ts >= (step.ended_ts or step.started_ts)]
            if not later_calls:
                continue
            docs = documents_of(step.output) or []
            prompt = _norm("\n".join(output_text(c.input_messages) for c in later_calls))
            snippets = [_norm(doc_text(d))[:80] for d in docs if not is_empty(doc_text(d))]
            snippets = [s for s in snippets if len(s) >= 12]
            if docs and not snippets:
                continue
            if any(s in prompt for s in snippets):
                continue
            if not docs:
                message = (
                    f"Retriever `{step.name}` returned nothing, and the model answered anyway "
                    f"({len(later_calls)} later LLM call(s) with no retrieved context)."
                )
            else:
                message = (
                    f"None of the {len(docs)} documents from `{step.name}` appear in the "
                    f"{len(later_calls)} later LLM input(s)."
                )
            yield self.finding(
                message, step=step, evidence={"documents": len(docs), "llm_calls": len(later_calls)}
            )
