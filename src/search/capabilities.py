from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class SearchCapabilityStatus:
    """Structured runtime view of search capabilities."""

    legacy_available: bool
    mx_route_available: bool
    comprehensive_intel_available: bool
    reasons: List[str] = field(default_factory=list)
