"""Minimal X5-F1 math core baseline.

This package provides small, explicit model runners for contest-time
baselines. It does not implement AutoML, paper generation, or release logic.
"""

from .registry import get_model_spec, list_models, run_model

__all__ = ["get_model_spec", "list_models", "run_model"]
