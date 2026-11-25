"""SARIF 2.1.0 for GitHub code scanning.

Each finding is a result; when the root-cause step's source is known the result points at that
``file:line``. Findings without a source point at ``tervic.toml`` (or the README) so that code
scanning, which requires a physical location, still accepts them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tervic._version import __version__
from tervic.analyze.engine import Report

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
LEVEL = {"error": "error", "warn": "warning", "info": "note"}


def _rules() -> list[dict[str, Any]]:
    from tervic.analyze.detectors import REGISTRY

    rules = []
    for cls in REGISTRY:
        rules.append(
            {
                "id": cls.id,
                "name": "".join(part.capitalize() for part in cls.name.split("-")),
                "shortDescription": {"text": f"{cls.id} {cls.name}"},
                "fullDescription": {"text": cls.description},
                "help": {"text": cls.how_to_fix, "markdown": f"**How to fix:** {cls.how_to_fix}"},
                "helpUri": f"https://github.com/prolixis/tervic/blob/main/docs/DETECTORS.md#{cls.id.lower()}",
                "defaultConfiguration": {"level": LEVEL[cls.severity]},
                "properties": {"tags": ["ai-agent", "silent-failure"]},
            }
        )
    rules.append(
        {
            "id": "TV801",
            "name": "JudgeFlag",
            "shortDescription": {"text": "TV801 judge-flag"},
            "fullDescription": {"text": "The optional LLM judge flagged an output."},
            "help": {"text": "Review the output; the judge is advisory."},
            "defaultConfiguration": {"level": "warning"},
        }
    )
    return rules


def _location(source: str | None, fallback: str) -> dict[str, Any]:
    if source and ":" in source:
        path, _, line = source.rpartition(":")
        if line.isdigit():
            return {
                "physicalLocation": {
                    "artifactLocation": {"uri": path.replace("\\", "/")},
                    "region": {"startLine": int(line)},
                }
            }
    return {"physicalLocation": {"artifactLocation": {"uri": fallback}, "region": {"startLine": 1}}}


def render(reports: list[Report]) -> str:
    fallback = "tervic.toml" if Path("tervic.toml").is_file() else "README.md"
    rules = _rules()
    index = {rule["id"]: i for i, rule in enumerate(rules)}
    results: list[dict[str, Any]] = []
    for report in reports:
        for finding in report.findings:
            group = next((g for g in report.groups if finding in g.findings), None)
            source = (group.source if group else None) or finding.source
            result: dict[str, Any] = {
                "ruleId": finding.detector_id,
                "level": LEVEL[finding.severity],
                "message": {"text": f"[{report.run_name}] {finding.message}"},
                "locations": [_location(source, fallback)],
                "partialFingerprints": {
                    "tervicFinding/v1": f"{report.run_name}:{finding.detector_id}:"
                    f"{finding.root_cause_step_name or finding.step_name}:{finding.field_path}",
                },
                "properties": {
                    "runId": report.run_id,
                    "step": finding.step_name,
                    "rootCauseStep": finding.root_cause_step_name,
                },
            }
            if finding.detector_id in index:
                result["ruleIndex"] = index[finding.detector_id]
            if finding.field_path:
                result["properties"]["field"] = finding.field_path
            results.append(result)
    doc = {
        "$schema": SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Tervic",
                        "version": __version__,
                        "semanticVersion": __version__,
                        "informationUri": "https://github.com/prolixis/tervic",
                        "rules": rules,
                    }
                },
                "results": results,
                "columnKind": "utf16CodeUnits",
            }
        ],
    }
    return json.dumps(doc, indent=2) + "\n"
