"""Fixed: the retriever filters on the right key and the answer is grounded in real documents."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from agent import DOCUMENTS, State, build  # noqa: E402


def retrieve(state: State) -> dict[str, Any]:
    docs = [doc for doc in DOCUMENTS if doc["product"] == state["product"]]
    return {"documents": docs}


app = build(retrieve)


def run(question: str = "What does Tervic do?") -> dict[str, Any]:
    result: dict[str, Any] = app.invoke({"question": question, "product": "tervic"})
    return result


if __name__ == "__main__":
    print(run()["answer"])
