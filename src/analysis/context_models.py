from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class PortfolioContext:
    """Explicit portfolio state for the stock currently being analyzed."""

    has_position: Optional[bool] = None
    quantity: Optional[float] = None
    cost_basis: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    valuation_currency: Optional[str] = None
    source: str = "portfolio_snapshot"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_position": self.has_position,
            "quantity": self.quantity,
            "cost_basis": self.cost_basis,
            "unrealized_pnl": self.unrealized_pnl,
            "valuation_currency": self.valuation_currency,
            "source": self.source,
        }
