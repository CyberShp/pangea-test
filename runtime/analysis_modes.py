"""Module-analysis execution modes.

The semantic workflow is the product default.  The exhaustive line-obligation
pipeline is retained only for explicitly created compatibility runs and for
historical runs that predate the persisted mode field.
"""
from __future__ import annotations

from typing import Any, Mapping


SEMANTIC = "semantic"
LINE_OBLIGATION = "line_obligation"
ALLOWED = frozenset({SEMANTIC, LINE_OBLIGATION})


class AnalysisModeError(ValueError):
    pass


def contract_mode(contract: Mapping[str, Any], *, legacy: bool = True) -> str:
    """Return the persisted mode, preserving resumability of old R2 Runs."""
    value = contract.get("analysis_execution_mode")
    if value is None and legacy:
        return LINE_OBLIGATION
    if value not in ALLOWED:
        raise AnalysisModeError("module analysis execution mode is invalid")
    return str(value)


def require(contract: Mapping[str, Any], expected: str, *, legacy: bool = True) -> None:
    if expected not in ALLOWED:
        raise AnalysisModeError("expected module analysis mode is invalid")
    actual = contract_mode(contract, legacy=legacy)
    if actual != expected:
        label = "semantic" if expected == SEMANTIC else "hidden line-obligation"
        raise AnalysisModeError(f"Run is not configured for {label} analysis")
