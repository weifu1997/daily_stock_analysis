from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CandidateLayerMetrics:
    code: str
    name: str = ""
    industry: Optional[str] = None
    close: Optional[float] = None
    pb: Optional[float] = None
    pe_ttm: Optional[float] = None
    roe: Optional[float] = None
    dividend_yield: Optional[float] = None
    return_20d: Optional[float] = None
    return_60d: Optional[float] = None
    return_120d: Optional[float] = None
    range_120d: Optional[float] = None
    position_120d: Optional[float] = None
    volatility_20d: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    ma_bullish: Optional[bool] = None
    ma20_up: Optional[bool] = None
    volume_ratio_20_120: Optional[float] = None
    macd_status: Optional[str] = None
    trend_status: Optional[str] = None
    trend_signal_score: Optional[float] = None
    data_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateScoreResult:
    code: str
    name: str = ""
    score: int = 0
    rating: str = "★★☆☆☆ 观察"
    trade_bias: str = "watch"
    excluded: bool = False
    factor_scores: Dict[str, float] = field(default_factory=dict)
    factor_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    core_logic: str = ""
    risk_flags: List[str] = field(default_factory=list)
    exclude_reason: Optional[str] = None
    no_trade_reason: Optional[str] = None
    entry_hint: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
