from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

DegradedStatus = Literal["skipped", "failed", "stale", "fallback"]


@dataclass(frozen=True)
class DegradedComponent:
    """Represents one degraded subsystem/component in a task run."""

    name: str
    status: DegradedStatus
    reason: str


@dataclass
class ExecutionReport:
    """Structured execution result for top-level analysis workflows."""

    success: bool
    degraded: bool = False
    fatal_error: Optional[str] = None
    degraded_components: List[DegradedComponent] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return not self.success

    def add_degraded_component(
        self,
        *,
        name: str,
        status: DegradedStatus,
        reason: str,
    ) -> None:
        self.degraded_components.append(
            DegradedComponent(name=name, status=status, reason=reason)
        )
        self.degraded = True

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def set_artifact(self, key: str, value: Any) -> None:
        self.artifacts[key] = value
