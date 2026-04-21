from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, TYPE_CHECKING

from .base_rules import ensure_decision_type_and_operation_advice_consistency, localized_operation_advice_for_signal
from .models import (
    AnalysisNormalizationContext,
    AnalysisNormalizationReport,
    RuleApplicationRecord,
)
from .portfolio_rules import HolderStructureRule, PortfolioContextRule

RISK_PENALTY_DEFAULT_THRESHOLD = 0.7


def _normalize_risk_penalty(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        normalized = float(value)
        if normalized != normalized:  # NaN
            return 0.0
        return normalized
    except Exception:
        return 0.0


def _derive_position_strength(result) -> str:
    decision = str(getattr(result, "decision_type", "hold") or "hold").strip().lower()
    risk_penalty = _normalize_risk_penalty(getattr(result, "risk_penalty", None))
    if decision == "buy" and risk_penalty < RISK_PENALTY_DEFAULT_THRESHOLD * 0.5:
        return "trial"
    if decision == "buy" and risk_penalty < RISK_PENALTY_DEFAULT_THRESHOLD:
        return "light_add"
    if decision == "buy" and risk_penalty >= RISK_PENALTY_DEFAULT_THRESHOLD:
        return "neutral"
    if decision == "sell":
        return "defense"
    return "neutral"


class RiskPenaltyGuardrailRule:
    name = "risk-penalty"

    @staticmethod
    def apply(result: "AnalysisResult", context: AnalysisNormalizationContext) -> None:
        risk_penalty = _normalize_risk_penalty(getattr(result, "risk_penalty", None))
        if str(getattr(result, "decision_type", "hold") or "hold").strip().lower() != "buy":
            return
        if risk_penalty < RISK_PENALTY_DEFAULT_THRESHOLD:
            return
        result.decision_type = "hold"
        result.operation_advice = localized_operation_advice_for_signal(
            "hold",
            getattr(result, "report_language", "zh"),
        )

    @staticmethod
    def describe_change(
        *,
        changed: bool,
        modified_fields: List[str],
        before: Any,
        after: Any,
        context: AnalysisNormalizationContext,
    ) -> tuple[str, str]:
        if not changed:
            return "info", "risk_penalty_no_change"
        if "decision_type" in modified_fields or "operation_advice" in modified_fields:
            return "hard_guardrail", "risk_penalty_buy_downgraded"
        return "hard_guardrail", "risk_penalty_adjusted"

if TYPE_CHECKING:
    from src.analyzer import AnalysisResult


class NormalizationRule(Protocol):
    name: str

    def apply(self, result: "AnalysisResult", context: AnalysisNormalizationContext) -> None:
        ...

    def describe_change(
        self,
        *,
        changed: bool,
        modified_fields: List[str],
        before: Any,
        after: Any,
        context: AnalysisNormalizationContext,
    ) -> tuple[str, str]:
        ...


@dataclass
class AnalysisNormalizationRuleChain:
    rules: List[NormalizationRule] = field(default_factory=list)

    def apply(
        self,
        result: Optional["AnalysisResult"],
        context: Optional[AnalysisNormalizationContext] = None,
    ) -> AnalysisNormalizationReport:
        if result is None:
            return AnalysisNormalizationReport()
        runtime_context = context or AnalysisNormalizationContext()
        records: List[RuleApplicationRecord] = []
        for rule in self.rules:
            before = _snapshot_result(result)
            rule.apply(result, runtime_context)
            after = _snapshot_result(result)
            modified_fields = _diff_paths(before, after)
            field_transitions = _collect_field_transitions(before, after, modified_fields)
            severity, reason_code = _describe_rule_change(
                rule,
                changed=bool(modified_fields),
                modified_fields=modified_fields,
                before=before,
                after=after,
                context=runtime_context,
            )
            records.append(
                RuleApplicationRecord(
                    rule_name=getattr(rule, "name", rule.__class__.__name__),
                    changed=bool(modified_fields),
                    severity=severity,
                    reason_code=reason_code,
                    modified_fields=modified_fields,
                    field_transitions=field_transitions,
                )
            )
        return AnalysisNormalizationReport(applied_rules=records)


class DecisionConsistencyRule:
    name = "decision-consistency"

    @staticmethod
    def apply(result: "AnalysisResult", context: AnalysisNormalizationContext) -> None:
        ensure_decision_type_and_operation_advice_consistency(result)

    @staticmethod
    def describe_change(
        *,
        changed: bool,
        modified_fields: List[str],
        before: Any,
        after: Any,
        context: AnalysisNormalizationContext,
    ) -> tuple[str, str]:
        if not changed:
            return "info", "decision_consistency_no_change"
        if "decision_type" in modified_fields:
            return "info", "decision_signal_normalized"
        if "operation_advice" in modified_fields:
            return "info", "operation_advice_backfilled"
        return "info", "decision_consistency_adjusted"


class PositionStrengthRule:
    name = "position-strength"

    @staticmethod
    def apply(result: "AnalysisResult", context: AnalysisNormalizationContext) -> None:
        result.position_strength = _derive_position_strength(result)

    @staticmethod
    def describe_change(
        *,
        changed: bool,
        modified_fields: List[str],
        before: Any,
        after: Any,
        context: AnalysisNormalizationContext,
    ) -> tuple[str, str]:
        if not changed:
            return "info", "position_strength_no_change"
        return "info", "position_strength_derived"


_DEFAULT_RULES: List[NormalizationRule] = [
    RiskPenaltyGuardrailRule(),
    DecisionConsistencyRule(),
    PortfolioContextRule(),
    HolderStructureRule(),
    DecisionConsistencyRule(),
    PositionStrengthRule(),
]


def build_default_rule_chain(
    *,
    extra_rules: Optional[List[NormalizationRule]] = None,
) -> AnalysisNormalizationRuleChain:
    base_rules = list(_DEFAULT_RULES)
    extras = list(extra_rules or [])
    duplicate_names = {
        getattr(rule, "name", rule.__class__.__name__)
        for rule in extras
        if any(
            getattr(existing, "name", existing.__class__.__name__) == getattr(rule, "name", rule.__class__.__name__)
            for existing in base_rules
        )
    }
    if duplicate_names:
        duplicate_name = sorted(duplicate_names)[0]
        raise ValueError(f"duplicate normalization rule: {duplicate_name}")
    return AnalysisNormalizationRuleChain(rules=base_rules + extras)


def normalize_analysis_result(
    result: Optional["AnalysisResult"],
    context: Optional[AnalysisNormalizationContext] = None,
) -> AnalysisNormalizationReport:
    return build_default_rule_chain().apply(result, context)


def _describe_rule_change(
    rule: NormalizationRule,
    *,
    changed: bool,
    modified_fields: List[str],
    before: Any,
    after: Any,
    context: AnalysisNormalizationContext,
) -> tuple[str, str]:
    describe = getattr(rule, "describe_change", None)
    if callable(describe):
        return describe(
            changed=changed,
            modified_fields=modified_fields,
            before=before,
            after=after,
            context=context,
        )
    if changed:
        return "info", f"{getattr(rule, 'name', rule.__class__.__name__)}_changed"
    return "info", f"{getattr(rule, 'name', rule.__class__.__name__)}_no_change"


def _snapshot_result(result: "AnalysisResult") -> Any:
    if hasattr(result, "to_dict"):
        return deepcopy(result.to_dict())
    return deepcopy(result)


def _get_value_by_path(payload: Any, dotted_path: str) -> Any:
    current = payload
    for part in str(dotted_path or "").split("."):
        if not part:
            continue
        if not isinstance(current, dict) or part not in current:
            return None
        current = current.get(part)
    return current


def _collect_field_transitions(before: Any, after: Any, modified_fields: List[str]) -> dict[str, dict[str, Any]]:
    transitions: dict[str, dict[str, Any]] = {}
    for field_path in modified_fields:
        transitions[field_path] = {
            "before": _get_value_by_path(before, field_path),
            "after": _get_value_by_path(after, field_path),
        }
    return transitions


def _diff_paths(before: Any, after: Any, prefix: str = "") -> List[str]:
    paths: List[str] = []
    if isinstance(before, dict) and isinstance(after, dict):
        keys = set(before) | set(after)
        for key in sorted(keys):
            new_prefix = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                paths.append(new_prefix)
                continue
            paths.extend(_diff_paths(before[key], after[key], new_prefix))
        return paths
    if before != after:
        paths.append(prefix)
    return paths
