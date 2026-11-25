"""Tervic: find which step of your AI agent silently broke, and stop it from shipping.

Two lines per framework::

    import tervic
    app = tervic.attach(graph.compile())        # LangGraph
    tervic.attach_openai_agents()               # OpenAI Agents SDK
    tervic.attach_crewai()                      # CrewAI

    @tervic.step                                # plain Python
    def retrieve(state): ...

    with tervic.run("support-agent") as run:
        pipeline(question)
    print(run.verdict)                          # "pass" | "warn" | "fail"

Everything is imported lazily so ``import tervic`` stays fast and needs only the core install.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tervic._version import __version__

if TYPE_CHECKING:
    from tervic.adapters.crewai import attach_crewai
    from tervic.adapters.decorator import run, step
    from tervic.adapters.langgraph import attach
    from tervic.adapters.llm_clients import attach_llm
    from tervic.adapters.openai_agents import attach_openai_agents
    from tervic.analyze.contracts import contract
    from tervic.analyze.engine import Report
    from tervic.analyze.findings import Finding
    from tervic.api import check, flush
    from tervic.config import Contract, TervicConfig, configure, get_config
    from tervic.core.recorder import add_exporter, remove_exporter
    from tervic.core.run import Run, current_run
    from tervic.export import Exporter

_LAZY: dict[str, tuple[str, str]] = {
    "attach": ("tervic.adapters.langgraph", "attach"),
    "attach_openai_agents": ("tervic.adapters.openai_agents", "attach_openai_agents"),
    "attach_crewai": ("tervic.adapters.crewai", "attach_crewai"),
    "attach_llm": ("tervic.adapters.llm_clients", "attach_llm"),
    "step": ("tervic.adapters.decorator", "step"),
    "run": ("tervic.adapters.decorator", "run"),
    "check": ("tervic.api", "check"),
    "flush": ("tervic.api", "flush"),
    "configure": ("tervic.config", "configure"),
    "get_config": ("tervic.config", "get_config"),
    "config": ("tervic.config", "get_config"),
    "Contract": ("tervic.config", "Contract"),
    "TervicConfig": ("tervic.config", "TervicConfig"),
    "contract": ("tervic.analyze.contracts", "contract"),
    "Report": ("tervic.analyze.engine", "Report"),
    "Finding": ("tervic.analyze.findings", "Finding"),
    "Run": ("tervic.core.run", "Run"),
    "current_run": ("tervic.core.run", "current_run"),
    "add_exporter": ("tervic.core.recorder", "add_exporter"),
    "remove_exporter": ("tervic.core.recorder", "remove_exporter"),
    "Exporter": ("tervic.export", "Exporter"),
}

__all__ = [
    "Contract",
    "Exporter",
    "Finding",
    "Report",
    "Run",
    "TervicConfig",
    "__version__",
    "add_exporter",
    "attach",
    "attach_crewai",
    "attach_llm",
    "attach_openai_agents",
    "check",
    "configure",
    "contract",
    "current_run",
    "flush",
    "get_config",
    "remove_exporter",
    "run",
    "step",
]


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module 'tervic' has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(target[0]), target[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
