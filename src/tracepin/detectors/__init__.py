"""Detector registry. Importing a module registers its detectors; add one file, one import."""

from tracepin.detectors import arguments, error_handling, hallucination, loops, performance  # noqa: F401
from tracepin.detectors.base import DETECTORS, Detector, Finding, RunDetector, Severity

__all__ = ["DETECTORS", "Detector", "Finding", "RunDetector", "Severity"]
