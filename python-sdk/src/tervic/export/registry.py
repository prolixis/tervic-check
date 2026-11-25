"""Which exporters run for a finished run: registered ones plus those enabled in config."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from tervic.core.recorder import registered_exporters
from tervic.core.safety import report_internal_error

if TYPE_CHECKING:
    from tervic.config import TervicConfig
    from tervic.export import Exporter

_cache: dict[tuple[object, ...], Exporter] = {}
_lock = threading.Lock()


def exporters_for(config: TervicConfig) -> list[Exporter]:
    found = registered_exporters()
    if config.webhook.url:
        key: tuple[object, ...] = ("webhook", config.webhook.url, config.webhook.format, config.webhook.on)
        with _lock:
            if key not in _cache:
                from tervic.export.webhook import WebhookExporter

                _cache[key] = WebhookExporter(
                    config.webhook.url,
                    format=config.webhook.format,
                    on=config.webhook.on,
                    timeout_s=config.webhook.timeout_s,
                )
            found.append(_cache[key])
    if config.otel.enabled:
        key = ("otel", config.otel.service_name)
        with _lock:
            if key not in _cache:
                try:
                    from tervic.export.otel import OtelExporter

                    _cache[key] = OtelExporter(service_name=config.otel.service_name)
                except Exception as exc:
                    report_internal_error("export.otel_init", exc)
            if key in _cache:
                found.append(_cache[key])
    return found
