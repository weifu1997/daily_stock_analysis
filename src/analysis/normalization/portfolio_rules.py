from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base_rules import localized_operation_advice_for_signal
from .models import AnalysisNormalizationContext

if TYPE_CHECKING:
    from src.analyzer import AnalysisResult

logger = logging.getLogger(__name__)


class PortfolioContextRule:
    """Normalize operation advice so non-holders never receive holding-only actions."""

    name = "portfolio-context"

    @staticmethod
    def apply(result: "AnalysisResult", context: AnalysisNormalizationContext) -> None:
        portfolio_context = context.portfolio_context
        has_position = None if portfolio_context is None else portfolio_context.has_position
        if has_position is True:
            return

        original_advice = str(getattr(result, "operation_advice", "") or "").strip()
        normalized_advice = original_advice.lower()
        advice_override = None
        decision_override = None

        if "加仓" in original_advice or normalized_advice in {"add", "add position", "increase", "increase position"}:
            decision_override = "buy"
        elif any(token in original_advice for token in ("减仓", "清仓")) or normalized_advice in {
            "trim",
            "reduce",
            "reduce position",
            "close",
            "close position",
            "exit",
            "exit position",
        }:
            decision_override = "sell"

        if decision_override is not None:
            advice_override = localized_operation_advice_for_signal(
                decision_override,
                getattr(result, "report_language", "zh"),
            )

        if advice_override is not None and advice_override != getattr(result, "operation_advice", None):
            logger.warning(
                "[%s] portfolio advice guardrail applied: has_position=%s, original=%s, adjusted=%s",
                getattr(result, "code", "unknown"),
                has_position,
                getattr(result, "operation_advice", None),
                advice_override,
            )
            result.operation_advice = advice_override
            result.decision_type = decision_override or getattr(result, "decision_type", "hold")

        dashboard = getattr(result, "dashboard", None)
        if not isinstance(dashboard, dict):
            return
        core_conclusion = dashboard.get("core_conclusion")
        if not isinstance(core_conclusion, dict):
            return
        position_advice = core_conclusion.get("position_advice")
        if not isinstance(position_advice, dict):
            return

        no_position_advice = position_advice.get("no_position")
        if isinstance(no_position_advice, str):
            replacement = sanitize_non_position_advice_text(no_position_advice)
            if replacement != no_position_advice:
                position_advice["no_position"] = replacement

    @staticmethod
    def describe_change(
        *,
        changed: bool,
        modified_fields: list[str],
        before,
        after,
        context: AnalysisNormalizationContext,
    ) -> tuple[str, str]:
        if not changed:
            return "info", "portfolio_context_no_change"
        if any(field == "operation_advice" for field in modified_fields):
            return "hard_guardrail", "portfolio_non_holder_action_adjusted"
        if any(field.startswith("dashboard.core_conclusion.position_advice.no_position") for field in modified_fields):
            return "warning", "portfolio_non_holder_text_adjusted"
        return "warning", "portfolio_context_adjusted"


def sanitize_non_position_advice_text(text: str) -> str:
    replacement = text
    replacement = replacement.replace("加仓", "买入")
    replacement = replacement.replace("减仓", "卖出")
    replacement = replacement.replace("清仓", "卖出")
    return replacement
