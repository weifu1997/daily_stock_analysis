from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import (
    CandidateLayerMetrics,
    CandidateScoreResult,
    L2_EXCLUDE_TRADE_BIAS,
    L2_RIGHT_SIDE_TRADE_BIAS,
    L2_SCORE_VERSION,
    L2_WATCH_TRADE_BIAS,
)


def _fmt(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def _breakdown_item(key: str, label: str, score: float, note: str) -> Dict[str, Any]:
    return {"key": key, "label": label, "score": score, "note": note}


def _build_no_trade_reason(risk_flags: List[str], excluded: bool, exclude_reason: Optional[str]) -> Optional[str]:
    if exclude_reason:
        return exclude_reason
    blocking_flags = [
        flag for flag in risk_flags
        if any(token in flag for token in ("ROE低于8%", "120日位置偏高", "成交比不足", "非多头排列", "MA20未向上"))
    ]
    if blocking_flags:
        return "；".join(blocking_flags[:3])
    if excluded:
        return "二筛未通过"
    return None


def _build_entry_hint(trade_bias: str, no_trade_reason: Optional[str], metrics: CandidateLayerMetrics) -> str:
    if trade_bias == L2_RIGHT_SIDE_TRADE_BIAS:
        return "右侧候选：等待放量突破后回踩不破再考虑，不追高。"
    if no_trade_reason:
        return f"先观察：{no_trade_reason}；未出现右侧确认前不作为买入依据。"
    if metrics.ma_bullish and metrics.ma20_up:
        return "趋势结构可观察，但仍需放量突破或回踩确认。"
    return "观察为主，等待右侧结构修复。"


def score_metrics(metrics: CandidateLayerMetrics) -> CandidateScoreResult:
    score = 0
    factor_scores: Dict[str, float] = {}
    factor_breakdown: List[Dict[str, Any]] = []
    risk_flags: List[str] = list(metrics.data_warnings)
    logic_parts: List[str] = []

    valuation_score = 0
    if metrics.pb is not None:
        if 0.8 <= metrics.pb <= 1.5:
            valuation_score += 3
        elif 0 < metrics.pb <= 1.8:
            valuation_score += 2
        elif metrics.pb > 2.5:
            risk_flags.append("PB偏高")
    if metrics.pe_ttm is not None:
        if 8 <= metrics.pe_ttm <= 20:
            valuation_score += 3
        elif 0 < metrics.pe_ttm <= 25:
            valuation_score += 2
        elif metrics.pe_ttm > 30:
            risk_flags.append("PE_TTM偏高")
    score += valuation_score
    factor_scores["valuation"] = valuation_score
    factor_breakdown.append(_breakdown_item(
        "valuation",
        "估值",
        valuation_score,
        f"PB {_fmt(metrics.pb)}，PE_TTM {_fmt(metrics.pe_ttm)}",
    ))

    quality_score = 0
    if metrics.roe is not None:
        if metrics.roe >= 10:
            quality_score += 4
        elif metrics.roe >= 8:
            quality_score += 2
        else:
            risk_flags.append("ROE低于8%")
            quality_score -= 3
    if metrics.dividend_yield is not None:
        if metrics.dividend_yield >= 4:
            quality_score += 3
        elif metrics.dividend_yield >= 2.5:
            quality_score += 2
    score += quality_score
    factor_scores["quality"] = quality_score
    factor_breakdown.append(_breakdown_item(
        "quality",
        "质量/分红",
        quality_score,
        f"ROE {_fmt(metrics.roe)}%，股息率{_fmt(metrics.dividend_yield)}%",
    ))

    position_score = 0
    if metrics.return_60d is not None:
        if 0 <= metrics.return_60d <= 12:
            position_score += 2
        elif metrics.return_60d > 20:
            risk_flags.append("60日涨幅偏高")
        elif metrics.return_60d < -8:
            risk_flags.append("60日趋势偏弱")
    if metrics.return_20d is not None:
        if -3 <= metrics.return_20d <= 8:
            position_score += 2
        elif metrics.return_20d > 12:
            risk_flags.append("20日涨幅偏高")
    if metrics.return_120d is not None:
        if -5 <= metrics.return_120d <= 20:
            position_score += 2
        elif metrics.return_120d > 20:
            risk_flags.append("120日涨幅偏高")
        elif metrics.return_120d < -8:
            risk_flags.append("120日趋势偏弱")
    if metrics.position_120d is not None:
        if 30 <= metrics.position_120d <= 75:
            position_score += 2
        elif metrics.position_120d > 90:
            risk_flags.append("120日位置偏高")
        elif metrics.position_120d < 25:
            risk_flags.append("120日位置偏低，趋势未修复")
    score += position_score
    factor_scores["position"] = position_score
    factor_breakdown.append(_breakdown_item(
        "position",
        "位置/涨幅",
        position_score,
        f"20日{_fmt(metrics.return_20d)}%，60日{_fmt(metrics.return_60d)}%，120日{_fmt(metrics.return_120d)}%，120日位置{_fmt(metrics.position_120d)}%",
    ))

    technical_score = 0
    if metrics.ma_bullish is True:
        technical_score += 3
    elif metrics.ma_bullish is False:
        risk_flags.append("非多头排列")
    if metrics.ma20_up is True:
        technical_score += 2
    elif metrics.ma20_up is False:
        risk_flags.append("MA20未向上")
    if metrics.macd_status and ("多头" in metrics.macd_status or "金叉" in metrics.macd_status or "零轴" in metrics.macd_status):
        technical_score += 1
    score += technical_score
    factor_scores["technical"] = technical_score
    factor_breakdown.append(_breakdown_item(
        "technical",
        "技术结构",
        technical_score,
        f"多头排列{metrics.ma_bullish}，MA20向上{metrics.ma20_up}，MACD {metrics.macd_status or 'N/A'}",
    ))

    volume_score = 0
    if metrics.volume_ratio_20_120 is not None:
        if metrics.volume_ratio_20_120 >= 1.05:
            volume_score += 2
        elif metrics.volume_ratio_20_120 < 0.8:
            risk_flags.append("成交比不足")
    score += volume_score
    factor_scores["volume"] = volume_score
    factor_breakdown.append(_breakdown_item(
        "volume",
        "量能",
        volume_score,
        f"20/120日成交比 {_fmt(metrics.volume_ratio_20_120)}",
    ))

    if metrics.pb is not None or metrics.pe_ttm is not None or metrics.roe is not None or metrics.dividend_yield is not None:
        logic_parts.append(
            f"PB {_fmt(metrics.pb)}，PE_TTM {_fmt(metrics.pe_ttm)}，ROE {_fmt(metrics.roe)}%，股息率{_fmt(metrics.dividend_yield)}%"
        )
    if metrics.return_60d is not None or metrics.return_20d is not None or metrics.position_120d is not None:
        logic_parts.append(
            f"60日涨幅{_fmt(metrics.return_60d)}%，20日涨幅{_fmt(metrics.return_20d)}%，120日位置{_fmt(metrics.position_120d)}%"
        )
    if metrics.ma_bullish is not None or metrics.ma20_up is not None or metrics.volume_ratio_20_120 is not None:
        logic_parts.append(
            f"多头排列{metrics.ma_bullish}，MA20向上{metrics.ma20_up}，成交比{_fmt(metrics.volume_ratio_20_120)}"
        )

    excluded = False
    exclude_reason = None
    if not logic_parts and risk_flags:
        score = max(score, 0)
    if score < 6 and "数据不足" not in risk_flags:
        excluded = True
        exclude_reason = "二筛分过低"

    if score >= 18 and not any(flag in risk_flags for flag in ("ROE低于8%", "120日位置偏高")):
        rating = "★★★★★ 强烈推荐"
    elif score >= 14:
        rating = "★★★★☆ 推荐"
    elif score >= 10:
        rating = "★★★☆☆ 关注"
    elif score >= 6:
        rating = "★★☆☆☆ 观察"
    else:
        rating = "★☆☆☆☆ 排除" if excluded else "★★☆☆☆ 观察"

    trade_bias = L2_WATCH_TRADE_BIAS
    if excluded:
        trade_bias = L2_EXCLUDE_TRADE_BIAS
    elif rating.startswith("★★★★★") and metrics.ma_bullish and metrics.ma20_up and (metrics.volume_ratio_20_120 or 0) >= 1.05:
        trade_bias = L2_RIGHT_SIDE_TRADE_BIAS

    deduped_risk_flags = list(dict.fromkeys(risk_flags))
    no_trade_reason = _build_no_trade_reason(deduped_risk_flags, excluded, exclude_reason)
    entry_hint = _build_entry_hint(trade_bias, no_trade_reason, metrics)
    observation_flag = trade_bias != L2_EXCLUDE_TRADE_BIAS

    score_breakdown = {
        "version": L2_SCORE_VERSION,
        "total_score": int(round(score)),
        "factor_scores": factor_scores,
        "factors": factor_breakdown,
        "risk_flags": deduped_risk_flags,
        "trade_bias": trade_bias,
        "excluded": excluded,
    }

    return CandidateScoreResult(
        code=metrics.code,
        name=metrics.name,
        score=int(round(score)),
        rating=rating,
        trade_bias=trade_bias,
        observation_flag=observation_flag,
        excluded=excluded,
        factor_scores=factor_scores,
        score_breakdown=score_breakdown,
        factor_breakdown=factor_breakdown,
        core_logic="；".join(logic_parts) if logic_parts else "数据不足，无法形成完整二筛结论",
        risk_flags=deduped_risk_flags,
        exclude_reason=exclude_reason,
        no_trade_reason=no_trade_reason,
        entry_hint=entry_hint,
        score_version=L2_SCORE_VERSION,
        metrics=metrics.to_dict(),
    )
