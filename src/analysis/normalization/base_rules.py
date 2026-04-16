from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.analyzer import AnalysisResult


def localized_operation_advice_for_signal(signal: str, report_language: str) -> str:
    normalized_language = "en" if str(report_language or "zh").strip().lower() == "en" else "zh"
    signal_map = {
        "buy": "Buy" if normalized_language == "en" else "买入",
        "hold": "Hold" if normalized_language == "en" else "持有",
        "sell": "Sell" if normalized_language == "en" else "卖出",
    }
    return signal_map.get(str(signal or "hold").strip().lower(), signal_map["hold"])


def ensure_decision_type_and_operation_advice_consistency(result: "AnalysisResult") -> None:
    from src.agent.protocols import normalize_decision_signal

    result.decision_type = normalize_decision_signal(getattr(result, "decision_type", "hold"))
    operation_advice = str(getattr(result, "operation_advice", "") or "").strip()
    if not operation_advice:
        result.operation_advice = localized_operation_advice_for_signal(
            result.decision_type,
            getattr(result, "report_language", "zh"),
        )
