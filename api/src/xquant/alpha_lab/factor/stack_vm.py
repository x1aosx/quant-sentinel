from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..errors import FormulaExecutionError
from .operator_registry import OPERATOR_REGISTRY
from .vocabulary import FORMULA_VOCAB


class FactorRuntime:
    """Execute feature/operator token programs with strict schema checks."""

    def __init__(self) -> None:
        self.feature_count = len(FORMULA_VOCAB.token_names) - len(
            OPERATOR_REGISTRY.names
        )
        self.operator_offset = self.feature_count
        self._operator_by_token = {
            self.operator_offset + index: spec
            for index, spec in enumerate(OPERATOR_REGISTRY.specs)
        }

    def evaluate(
        self,
        formula_tokens: Sequence[int],
        features: np.ndarray,
        schema_version: str,
    ) -> np.ndarray:
        FORMULA_VOCAB.verify(schema_version)
        if features.ndim != 3:
            raise FormulaExecutionError("features must have shape [N, F, T]")
        if features.shape[1] != self.feature_count:
            raise FormulaExecutionError(
                f"feature dimension mismatch: {features.shape[1]} != {self.feature_count}"
            )
        if not formula_tokens:
            raise FormulaExecutionError("formula cannot be empty")
        stack: list[np.ndarray] = []
        for token in formula_tokens:
            try:
                token_id = int(token)
            except (TypeError, ValueError) as exc:
                raise FormulaExecutionError(f"invalid token: {token!r}") from exc
            if 0 <= token_id < self.feature_count:
                stack.append(features[:, token_id, :])
                continue
            spec = self._operator_by_token.get(token_id)
            if spec is None:
                raise FormulaExecutionError(f"token out of range: {token_id}")
            if len(stack) < spec.arity:
                raise FormulaExecutionError(
                    f"stack underflow for {spec.name}: needs {spec.arity}"
                )
            arguments = [stack.pop() for _ in range(spec.arity)]
            arguments.reverse()
            if any(argument.shape != features.shape[0:1] + features.shape[2:] for argument in arguments):
                raise FormulaExecutionError(f"operator input shape mismatch: {spec.name}")
            try:
                result = spec.execute(*arguments)
            except (ArithmeticError, IndexError, TypeError, ValueError) as exc:
                raise FormulaExecutionError(
                    f"operator failed: {spec.name}: {exc}"
                ) from exc
            if result.shape != features.shape[0:1] + features.shape[2:]:
                raise FormulaExecutionError(
                    f"operator returned invalid shape: {spec.name}: {result.shape}"
                )
            stack.append(np.asarray(result, dtype=np.float64))
        if len(stack) != 1:
            raise FormulaExecutionError(
                f"formula left {len(stack)} values on the stack, expected 1"
            )
        result = np.nan_to_num(stack[0], nan=0.0, posinf=0.0, neginf=0.0)
        if not np.isfinite(result).all():
            raise FormulaExecutionError("formula output is not finite")
        return result

    def expression(self, formula_tokens: Sequence[int]) -> str:
        stack: list[str] = []
        for token in formula_tokens:
            token_id = int(token)
            if 0 <= token_id < self.feature_count:
                stack.append(FORMULA_VOCAB.token_names[token_id])
                continue
            spec = self._operator_by_token.get(token_id)
            if spec is None or len(stack) < spec.arity:
                raise FormulaExecutionError("formula cannot be decoded")
            arguments = [stack.pop() for _ in range(spec.arity)]
            arguments.reverse()
            stack.append(f"{spec.name}({', '.join(arguments)})")
        if len(stack) != 1:
            raise FormulaExecutionError("formula cannot be decoded")
        return stack[0]
