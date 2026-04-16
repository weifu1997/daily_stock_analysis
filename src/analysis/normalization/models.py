from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from src.analysis.context_models import PortfolioContext


@dataclass(frozen=True)
class AnalysisNormalizationContext:
    """Runtime normalization inputs that are external to the raw LLM result."""

    portfolio_context: Optional[PortfolioContext] = None


@dataclass(frozen=True)
class RuleApplicationRecord:
    rule_name: str
    changed: bool
    severity: str = "info"
    reason_code: str = "no_change"
    modified_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule_name": self.rule_name,
            "changed": self.changed,
            "severity": self.severity,
            "reason_code": self.reason_code,
            "modified_fields": list(self.modified_fields),
        }


@dataclass(frozen=True)
class AnalysisNormalizationReport:
    applied_rules: List[RuleApplicationRecord] = field(default_factory=list)

    @property
    def total_rules(self) -> int:
        return len(self.applied_rules)

    @property
    def changed_rule_count(self) -> int:
        return sum(1 for record in self.applied_rules if record.changed)

    @property
    def modified_fields(self) -> List[str]:
        ordered: List[str] = []
        seen = set()
        for record in self.applied_rules:
            for field_name in record.modified_fields:
                if field_name in seen:
                    continue
                seen.add(field_name)
                ordered.append(field_name)
        return ordered

    @property
    def reason_codes(self) -> List[str]:
        ordered: List[str] = []
        seen = set()
        for record in self.applied_rules:
            if record.reason_code in seen:
                continue
            seen.add(record.reason_code)
            ordered.append(record.reason_code)
        return ordered

    @property
    def max_severity(self) -> str:
        ranking = {"info": 0, "warning": 1, "hard_guardrail": 2}
        max_level = "info"
        max_rank = -1
        for record in self.applied_rules:
            rank = ranking.get(record.severity, 0)
            if rank > max_rank:
                max_rank = rank
                max_level = record.severity
        return max_level if self.applied_rules else "info"

    def to_dict(self) -> dict:
        return {
            "applied_rules": [record.to_dict() for record in self.applied_rules],
            "total_rules": self.total_rules,
            "changed_rule_count": self.changed_rule_count,
            "modified_fields": list(self.modified_fields),
            "reason_codes": list(self.reason_codes),
            "max_severity": self.max_severity,
        }
