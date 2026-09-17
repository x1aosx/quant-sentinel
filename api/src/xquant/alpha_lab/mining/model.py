from __future__ import annotations

from .policy import FormulaPolicy, NumpyPolicy, PolicyUpdate

MiningModel = NumpyPolicy
PolicyModel = NumpyPolicy

__all__ = [
    "FormulaPolicy",
    "MiningModel",
    "NumpyPolicy",
    "PolicyModel",
    "PolicyUpdate",
]
