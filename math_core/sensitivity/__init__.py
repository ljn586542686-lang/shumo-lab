"""Sensitivity / robustness perturbation matrix for X5-F5.2.

Capability boundary: generate local sensitivity evidence for a single
selected model over a declared parameter grid.  This module does NOT:
  - perform model tournament (reserved for F5.3)
  - select the best model
  - judge academic validity
  - perturb solver hyperparameters (method, maxiter, tol, etc.)
"""

from . import perturbation_matrix
