"""Built-in detectors. Importing this package registers all of them."""

from tervic.analyze.detectors import (
    baseline_diff,
    control_flow,
    llm,
    output_integrity,
    retrieval,
    safety,
    swallowed_errors,
)
from tervic.analyze.findings import REGISTRY, Detector

__all__ = [
    "REGISTRY",
    "Detector",
    "baseline_diff",
    "control_flow",
    "llm",
    "output_integrity",
    "retrieval",
    "safety",
    "swallowed_errors",
]
