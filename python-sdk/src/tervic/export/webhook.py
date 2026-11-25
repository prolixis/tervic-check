"""Webhook exporter: POST a JSON finding summary (or a Slack message) when a run fails."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from tervic.analyze.engine import Report
    from tervic.core.events import Event

Post = Callable[[str, bytes, float], None]


def _post(url: str, body: bytes, timeout: float) -> None:
    request = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout):
        pass


def summary(report: Report) -> dict[str, Any]:
    return {
        "tervic_version": report.tervic_version,
        "run_id": report.run_id,
        "run_name": report.run_name,
        "verdict": report.verdict,
        "status": report.status,
        "git_sha": report.git_sha,
        "counts": report.counts,
        "root_causes": [
            {
                "step": g.root_cause_step_name,
                "source": g.source,
                "severity": g.severity,
                "detectors": g.detector_ids,
                "message": g.title,
            }
            for g in report.groups
        ],
    }


def slack_message(report: Report) -> dict[str, Any]:
    icon = {"fail": ":red_circle:", "warn": ":large_yellow_circle:"}.get(report.verdict, ":white_check_mark:")
    lines = [f"{icon} *Tervic: {report.run_name}* — {report.verdict.upper()} (run `{report.run_id}`)"]
    for group in report.groups[:5]:
        where = f" (`{group.source}`)" if group.source else ""
        step = group.root_cause_step_name or "run"
        lines.append(f"• *{step}*{where}: {group.title} [{', '.join(group.detector_ids)}]")
    return {"text": "\n".join(lines)}


class WebhookExporter:
    def __init__(
        self,
        url: str,
        *,
        format: Literal["json", "slack"] = "json",
        on: Literal["fail", "warn", "always"] = "fail",
        timeout_s: float = 5.0,
        post: Post | None = None,
    ) -> None:
        self.url = url
        self.format = format
        self.on = on
        self.timeout_s = timeout_s
        self.post = post or _post
        self.sent = 0

    def should_send(self, report: Report) -> bool:
        if self.on == "always":
            return True
        if self.on == "warn":
            return report.verdict in ("warn", "fail")
        return report.verdict == "fail"

    def export(self, events: list[Event], report: Report) -> None:
        if not self.should_send(report):
            return
        payload = slack_message(report) if self.format == "slack" else summary(report)
        self.post(self.url, json.dumps(payload).encode("utf-8"), self.timeout_s)
        self.sent += 1
