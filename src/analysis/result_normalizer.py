from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from src.analysis.context_models import PortfolioContext
from src.analysis.normalization import (
    AnalysisNormalizationContext,
    AnalysisNormalizationReport,
    normalize_analysis_result,
)
from src.analysis.normalization.portfolio_rules import sanitize_non_position_advice_text

if TYPE_CHECKING:
    from src.analyzer import AnalysisResult


def normalize_analysis_result_for_portfolio_context(
    result: Optional["AnalysisResult"],
    portfolio_context: Optional[PortfolioContext],
) -> AnalysisNormalizationReport:
    return normalize_analysis_result(
        result,
        AnalysisNormalizationContext(portfolio_context=portfolio_context),
    )


__all__ = [
    "normalize_analysis_result_for_portfolio_context",
    "sanitize_non_position_advice_text",
]
