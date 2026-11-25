"""JUnit XML for CI systems such as GitLab and Jenkins: one test suite per run."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from tervic.analyze.engine import Report


def render(reports: list[Report]) -> str:
    suites = ET.Element("testsuites", name="tervic")
    total = failures = 0
    for report in reports:
        cases = ["verdict"] + [g.root_cause_step_name or "run" for g in report.groups]
        suite = ET.SubElement(
            suites,
            "testsuite",
            name=f"tervic.{report.run_name}",
            tests=str(len(cases)),
            failures="0",
            errors="0",
            skipped="0",
            time=f"{report.stats.duration_ms / 1000:.3f}",
        )
        suite_failures = 0
        verdict = ET.SubElement(
            suite,
            "testcase",
            classname=f"tervic.{report.run_name}",
            name=f"verdict ({report.run_id})",
            time=f"{report.stats.duration_ms / 1000:.3f}",
        )
        if report.verdict == "fail":
            suite_failures += 1
            failure = ET.SubElement(
                verdict, "failure", message=f"Tervic verdict: fail ({report.counts})", type="TervicFail"
            )
            failure.text = "\n".join(f"[{f.detector_id}] {f.message}" for f in report.findings)
        for group in report.groups:
            case = ET.SubElement(
                suite,
                "testcase",
                classname=f"tervic.{report.run_name}",
                name=f"{group.root_cause_step_name or 'run'}: {', '.join(group.detector_ids)}",
                time="0",
            )
            body = "\n".join(f"[{f.detector_id}] {f.message}" for f in group.findings)
            if group.source:
                body += f"\nsource: {group.source}"
            if group.severity == "error" or (report.fail_on == "warn" and group.severity == "warn"):
                suite_failures += 1
                failure = ET.SubElement(
                    case, "failure", message=group.title, type=", ".join(group.detector_ids)
                )
                failure.text = body
            else:
                out = ET.SubElement(case, "system-out")
                out.text = body
        suite.set("failures", str(suite_failures))
        total += len(cases)
        failures += suite_failures
    suites.set("tests", str(total))
    suites.set("failures", str(failures))
    ET.indent(suites)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suites, encoding="unicode") + "\n"
