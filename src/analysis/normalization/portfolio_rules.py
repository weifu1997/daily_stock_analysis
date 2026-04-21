from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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


class HolderStructureRule:
    """Use holder structure + intelligence balance to suppress over-aggressive conclusions."""

    name = "holder-structure"

    _SELL_TOKENS = ("清仓", "卖出", "exit", "reduce", "trim")
    _SELL_EXIT_TOKENS = ("清仓", "卖出", "exit")
    _SELL_REDUCTION_TOKENS = ("减仓", "reduce", "trim")

    @staticmethod
    def _normalize_sell_action(result: "AnalysisResult") -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
        original_advice = str(getattr(result, "operation_advice", "") or "").strip()
        if not original_advice:
            return False, None, None, None
        lowered = original_advice.lower()
        if not any(token in original_advice or token in lowered for token in HolderStructureRule._SELL_TOKENS):
            return False, None, None, None
        if any(token in original_advice or token in lowered for token in HolderStructureRule._SELL_EXIT_TOKENS):
            reason_code = "sell_action_exit"
        elif any(token in original_advice or token in lowered for token in HolderStructureRule._SELL_REDUCTION_TOKENS):
            reason_code = "sell_action_reduce"
        else:
            reason_code = "sell_action_standardized"
        if original_advice == "卖出" and str(getattr(result, "decision_type", "") or "").strip().lower() == "sell":
            return False, "卖出", reason_code, original_advice
        return True, "卖出", reason_code, original_advice

    @staticmethod
    def _apply_sell_action_normalization(result: "AnalysisResult") -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
        changed, target_advice, reason_code, before_advice = HolderStructureRule._normalize_sell_action(result)
        if not target_advice:
            return False, None, None, None
        if changed:
            result.operation_advice = target_advice
            if str(getattr(result, "decision_type", "") or "").strip().lower() != "sell":
                result.decision_type = "sell"
        return changed, target_advice, reason_code, before_advice

    @staticmethod
    def apply(result: "AnalysisResult", context: AnalysisNormalizationContext) -> None:
        dashboard = getattr(result, "dashboard", None)
        if not isinstance(dashboard, dict):
            return

        data_perspective = dashboard.get("data_perspective")
        institution_structure = data_perspective.get("institution_structure") if isinstance(data_perspective, dict) else None
        if not isinstance(institution_structure, dict):
            return
        holder_bias = str(institution_structure.get("holder_structure_bias") or "").strip()
        if not holder_bias:
            return

        normalized_decision = str(getattr(result, "decision_type", "") or "").strip().lower()
        if normalized_decision != "buy":
            changed, _, _, _ = HolderStructureRule._apply_sell_action_normalization(result)
            if changed:
                return
            return
        HolderStructureRule._apply_sell_action_normalization(result)

        intelligence = dashboard.get("intelligence") if isinstance(dashboard.get("intelligence"), dict) else {}
        risk_alerts = intelligence.get("risk_alerts") if isinstance(intelligence.get("risk_alerts"), list) else []
        positive_catalysts = intelligence.get("positive_catalysts") if isinstance(intelligence.get("positive_catalysts"), list) else []
        latest_news = str(intelligence.get("latest_news") or "").strip()
        earnings_outlook = str(intelligence.get("earnings_outlook") or "").strip()

        if holder_bias == "分散" and len([item for item in risk_alerts if str(item).strip()]) >= 2:
            messages = _holder_structure_guardrail_messages(
                "distributed_risk",
                getattr(result, "report_language", "zh"),
            )
            _downgrade_buy_to_hold(
                result,
                one_sentence=messages["one_sentence"],
                no_position_advice=messages["no_position"],
                has_position_advice=messages["has_position"],
            )
            return

        intel_has_catalyst = bool([item for item in positive_catalysts if str(item).strip()]) or bool(latest_news) or bool(earnings_outlook)
        if holder_bias == "集中" and not intel_has_catalyst:
            messages = _holder_structure_guardrail_messages(
                "concentrated_no_intel",
                getattr(result, "report_language", "zh"),
            )
            _downgrade_buy_to_hold(
                result,
                one_sentence=messages["one_sentence"],
                no_position_advice=messages["no_position"],
                has_position_advice=messages["has_position"],
            )

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
            return "info", "holder_structure_no_change"

        after_dict = after if isinstance(after, dict) else {}
        dashboard = after_dict.get("dashboard") if isinstance(after_dict, dict) else {}
        intelligence = dashboard.get("intelligence") if isinstance(dashboard, dict) else {}
        data_perspective = dashboard.get("data_perspective") if isinstance(dashboard, dict) else {}
        institution_structure = data_perspective.get("institution_structure") if isinstance(data_perspective, dict) else {}
        holder_bias = str(institution_structure.get("holder_structure_bias") or "")
        risk_alerts = intelligence.get("risk_alerts") if isinstance(intelligence.get("risk_alerts"), list) else []
        positive_catalysts = intelligence.get("positive_catalysts") if isinstance(intelligence.get("positive_catalysts"), list) else []
        latest_news = str(intelligence.get("latest_news") or "").strip()
        earnings_outlook = str(intelligence.get("earnings_outlook") or "").strip()

        if holder_bias == "分散" and len([item for item in risk_alerts if str(item).strip()]) >= 2:
            return "hard_guardrail", "holder_structure_distributed_risk_buy_downgraded"
        if holder_bias == "集中" and not (bool([item for item in positive_catalysts if str(item).strip()]) or latest_news or earnings_outlook):
            return "warning", "holder_structure_concentrated_no_intel_buy_softened"
        return "warning", "holder_structure_adjusted"


def _holder_structure_guardrail_messages(case: str, report_language: str) -> dict[str, str]:
    normalized_language = "en" if str(report_language or "zh").strip().lower() == "en" else "zh"
    messages = {
        "distributed_risk": {
            "zh": {
                "one_sentence": "筹码分散且风险偏多，暂不宜激进买入，先观望确认。",
                "no_position": "空仓者以观望为主，等待风险出清或新催化确认。",
                "has_position": "持仓者控制预期，先观察风险释放与承接是否改善。",
            },
            "en": {
                "one_sentence": "Holder structure looks dispersed and risks are piling up. Avoid aggressive buying until conditions stabilize.",
                "no_position": "Stay on the sidelines until risks clear or new catalysts appear.",
                "has_position": "Existing holders should stay measured and watch whether risk pressure and buying support improve.",
            },
        },
        "concentrated_no_intel": {
            "zh": {
                "one_sentence": "筹码虽偏集中，但消息催化不足，先等待进一步确认。",
                "no_position": "空仓者不宜仅凭筹码集中就激进买入，等待消息或业绩催化。",
                "has_position": "持仓者可以继续跟踪，但不要在缺少催化时过度乐观。",
            },
            "en": {
                "one_sentence": "Holder concentration looks constructive, but catalysts are still missing. Wait for confirmation.",
                "no_position": "Do not chase solely on holder concentration; wait for news or earnings catalysts.",
                "has_position": "Existing holders can keep tracking, but should avoid overconfidence without catalysts.",
            },
        },
    }
    return messages.get(case, messages["concentrated_no_intel"])[normalized_language]



def _downgrade_buy_to_hold(
    result: "AnalysisResult",
    *,
    one_sentence: str,
    no_position_advice: str,
    has_position_advice: str,
) -> None:
    result.decision_type = "hold"
    result.operation_advice = localized_operation_advice_for_signal(
        "hold",
        getattr(result, "report_language", "zh"),
    )
    dashboard = getattr(result, "dashboard", None)
    if not isinstance(dashboard, dict):
        return
    core_conclusion = dashboard.get("core_conclusion")
    if not isinstance(core_conclusion, dict):
        return
    if one_sentence:
        core_conclusion["one_sentence"] = one_sentence
    position_advice = core_conclusion.get("position_advice")
    if not isinstance(position_advice, dict):
        return
    position_advice["no_position"] = no_position_advice
    position_advice["has_position"] = has_position_advice


def sanitize_non_position_advice_text(text: str) -> str:
    replacement = text
    replacement = replacement.replace("加仓", "买入")
    replacement = replacement.replace("减仓", "卖出")
    replacement = replacement.replace("清仓", "卖出")
    return replacement
