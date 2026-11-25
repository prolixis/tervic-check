"""Helpers for the example tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_example(path: Path) -> ModuleType:
    """Import an example file under a unique module name (the examples share file names)."""
    name = "example_" + "_".join(path.with_suffix("").parts[-2:])
    for cached in ("agent",):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check_cli(*args: str) -> tuple[int, str]:
    """Run the CLI in-process. Frameworks such as CrewAI print from background threads, so wait for
    them first and, for --json, drop anything printed before the JSON document."""
    from typer.testing import CliRunner

    import tervic
    from tervic.cli import app

    tervic.flush()
    result = CliRunner().invoke(app, list(args))
    output = result.output
    if "--json" in args:
        lines = output.splitlines()
        start = next((i for i, line in enumerate(lines) if line.startswith(("{", "["))), 0)
        output = "\n".join(lines[start:])
    return result.exit_code, output
