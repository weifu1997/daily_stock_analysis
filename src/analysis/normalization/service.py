from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, TYPE_CHECKING

from .base_rules import ensure_decision_type_and_operation_advice_consistency
from .models import (
    AnalysisNormalizationContext,
    AnalysisNormalizationReport,
    RuleApplicationRecord,
)
from .portfolio_rules import PortfolioContextRule

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


_DEFAULT_RULES: List[NormalizationRule] = [
    DecisionConsistencyRule(),
    PortfolioContextRule(),
    DecisionConsistencyRule(),
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


def _diff_paths(before: Any, after: Any, prefix: str = "") -> List[str]:
    if before == after:
        return []

    if isinstance(before, dict) and isinstance(after, dict):
        paths: List[str] = []
        keys = set(before.keys()) | set(after.keys())
        for key in sorted(keys):
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                paths.append(next_prefix)
                continue
            paths.extend(_diff_paths(before[key], after[key], next_prefix))
        return paths

    return [prefix or "<root>"]
