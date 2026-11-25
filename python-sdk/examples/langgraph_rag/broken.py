"""Broken: the retriever filters on the wrong key, silently returns [] and the model answers anyway.

Expected: TV301 empty-retrieval and TV302 retrieval-unused, root cause = `retrieve`.
Run:  python examples/langgraph_rag/broken.py && tervic check
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from agent import DOCUMENTS, State, build  # noqa: E402


def retrieve(state: State) -> dict[str, Any]:
    # Bug: documents are tagged with "product", but we filter on "category", which never matches.
    docs = [doc for doc in DOCUMENTS if doc.get("category") == state["product"]]
    return {"documents": docs}


app = build(retrieve)


def run(question: str = "What does Tervic do?") -> dict[str, Any]:
    result: dict[str, Any] = app.invoke({"question": question, "product": "tervic"})
    return result


if __name__ == "__main__":
    print(run()["answer"])
