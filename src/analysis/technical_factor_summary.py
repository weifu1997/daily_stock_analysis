from __future__ import annotations

from typing import Any, Dict, List, Optional


STK_FACTOR_V1_FIELDS = (
    "turnover_rate",
    "volume_ratio",
    "updays",
    "downdays",
    "ma_qfq_20",
    "ma_qfq_60",
    "macd_qfq",
    "rsi_qfq_12",
    "boll_mid_qfq",
    "atr_qfq",
)


def summarize_stk_factor_snapshot(
    snapshot: Dict[str, Any],
    close_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    将 stk_factor_pro 单日快照压缩为项目可消费的技术状态摘要。

    设计原则：
    - fail-open：任何异常返回 None，不打断主分析流程
    - v1 仅依赖精选 10 个字段 + close_price
    - 仅消费 qfq 口径字段
    """
    if not snapshot or not isinstance(snapshot, dict):
        return None

    try:
        trade_date = _to_str(snapshot.get("trade_date"))
        close = _to_float(close_price)
        if close is None:
            close = _to_float(snapshot.get("close"))

        metrics = {
            "turnover_rate": _to_float(snapshot.get("turnover_rate")),
            "volume_ratio": _to_float(snapshot.get("volume_ratio")),
            "updays": _to_int(snapshot.get("updays")),
            "downdays": _to_int(snapshot.get("downdays")),
            "ma_qfq_20": _to_float(snapshot.get("ma_qfq_20")),
            "ma_qfq_60": _to_float(snapshot.get("ma_qfq_60")),
            "macd_qfq": _to_float(snapshot.get("macd_qfq")),
            "rsi_qfq_12": _to_float(snapshot.get("rsi_qfq_12")),
            "boll_mid_qfq": _to_float(snapshot.get("boll_mid_qfq")),
            "atr_qfq": _to_float(snapshot.get("atr_qfq")),
        }

        states = {
            "activity_state": _build_activity_state(
                turnover_rate=metrics["turnover_rate"],
                volume_ratio=metrics["volume_ratio"],
            ),
            "streak_state": _build_streak_state(
                updays=metrics["updays"],
                downdays=metrics["downdays"],
            ),
            "trend_state": _build_trend_state(
                close=close,
                ma20=metrics["ma_qfq_20"],
                ma60=metrics["ma_qfq_60"],
                boll_mid=metrics["boll_mid_qfq"],
            ),
            "momentum_state": _build_momentum_state(
                macd=metrics["macd_qfq"],
                rsi12=metrics["rsi_qfq_12"],
            ),
            "volatility_state": _build_volatility_state(
                atr=metrics["atr_qfq"],
                close=close,
            ),
        }

        flags = _build_flags(
            close=close,
            turnover_rate=metrics["turnover_rate"],
            volume_ratio=metrics["volume_ratio"],
            updays=metrics["updays"],
            downdays=metrics["downdays"],
            ma20=metrics["ma_qfq_20"],
            ma60=metrics["ma_qfq_60"],
            macd=metrics["macd_qfq"],
            rsi12=metrics["rsi_qfq_12"],
            boll_mid=metrics["boll_mid_qfq"],
            atr=metrics["atr_qfq"],
        )

        narrative = _build_narrative(states)

        return {
            "version": "v1",
            "source": "tushare_stk_factor_pro",
            "trade_date": trade_date,
            "close_price": close,
            "states": states,
            "flags": flags,
            "metrics": metrics,
            "narrative": narrative,
        }

    except Exception:
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None



def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None



def _to_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()



def _build_activity_state(
    turnover_rate: Optional[float],
    volume_ratio: Optional[float],
) -> str:
    if turnover_rate is None and volume_ratio is None:
        return "unknown"

    if turnover_rate is not None and turnover_rate >= 8:
        if volume_ratio is not None and volume_ratio >= 1.5:
            return "high_turnover_volume_expanding"
        return "high_turnover"

    if turnover_rate is not None and turnover_rate <= 2:
        if volume_ratio is not None and volume_ratio <= 0.8:
            return "low_turnover_volume_contracting"
        return "low_turnover"

    if volume_ratio is not None and volume_ratio >= 1.5:
        return "normal_turnover_volume_expanding"

    if volume_ratio is not None and volume_ratio <= 0.8:
        return "normal_turnover_volume_contracting"

    return "normal_activity"



def _build_streak_state(
    updays: Optional[int],
    downdays: Optional[int],
) -> str:
    if updays is not None and updays >= 5:
        return "extended_up_streak"
    if updays is not None and updays >= 2:
        return "short_up_streak"

    if downdays is not None and downdays >= 5:
        return "extended_down_streak"
    if downdays is not None and downdays >= 2:
        return "short_down_streak"

    return "no_clear_streak"



def _build_trend_state(
    close: Optional[float],
    ma20: Optional[float],
    ma60: Optional[float],
    boll_mid: Optional[float] = None,
) -> str:
    if close is None or ma20 is None or ma60 is None:
        return "trend_unknown"

    above_ma20 = close > ma20
    above_ma60 = close > ma60

    if above_ma20 and above_ma60:
        return "above_ma20_above_ma60"
    if (not above_ma20) and above_ma60:
        return "below_ma20_above_ma60"
    if above_ma20 and (not above_ma60):
        return "above_ma20_below_ma60"
    return "below_ma20_below_ma60"



def _build_momentum_state(
    macd: Optional[float],
    rsi12: Optional[float],
) -> str:
    macd_state = "unknown"
    if macd is not None:
        if macd > 0:
            macd_state = "positive"
        elif macd < 0:
            macd_state = "negative"
        else:
            macd_state = "flat"

    rsi_state = "unknown"
    if rsi12 is not None:
        if rsi12 >= 70:
            rsi_state = "overbought"
        elif rsi12 <= 30:
            rsi_state = "oversold"
        elif rsi12 >= 55:
            rsi_state = "strong"
        elif rsi12 <= 45:
            rsi_state = "weak"
        else:
            rsi_state = "neutral"

    if macd_state == "positive" and rsi_state in {"strong", "overbought"}:
        return "bullish"
    if macd_state == "negative" and rsi_state in {"weak", "oversold"}:
        return "bearish"
    if rsi_state == "neutral":
        return "neutral"
    return "mixed"



def _build_volatility_state(
    atr: Optional[float],
    close: Optional[float],
) -> str:
    if atr is None or close is None or close <= 0:
        return "unknown"

    atr_ratio = atr / close

    if atr_ratio >= 0.05:
        return "high_volatility"
    if atr_ratio <= 0.02:
        return "low_volatility"
    return "normal_volatility"



def _build_flags(
    close: Optional[float],
    turnover_rate: Optional[float],
    volume_ratio: Optional[float],
    updays: Optional[int],
    downdays: Optional[int],
    ma20: Optional[float],
    ma60: Optional[float],
    macd: Optional[float],
    rsi12: Optional[float],
    boll_mid: Optional[float],
    atr: Optional[float],
) -> List[str]:
    flags: List[str] = []

    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            flags.append("volume_expanding")
        elif volume_ratio <= 0.8:
            flags.append("volume_contracting")

    if turnover_rate is not None:
        if turnover_rate >= 8:
            flags.append("high_turnover")
        elif turnover_rate <= 2:
            flags.append("low_turnover")

    if updays is not None and updays >= 3:
        flags.append("multi_day_up")
    if downdays is not None and downdays >= 3:
        flags.append("multi_day_down")

    if close is not None and ma20 is not None:
        flags.append("above_ma20" if close > ma20 else "below_ma20")

    if close is not None and ma60 is not None:
        flags.append("above_ma60" if close > ma60 else "below_ma60")

    if macd is not None:
        if macd > 0:
            flags.append("macd_positive")
        elif macd < 0:
            flags.append("macd_negative")

    if rsi12 is not None:
        if rsi12 >= 70:
            flags.append("rsi_overbought")
        elif rsi12 <= 30:
            flags.append("rsi_oversold")

    if close is not None and boll_mid is not None:
        if close > boll_mid:
            flags.append("above_boll_mid")
        elif close < boll_mid:
            flags.append("below_boll_mid")

    if atr is not None and close is not None and close > 0:
        atr_ratio = atr / close
        if atr_ratio >= 0.05:
            flags.append("high_volatility")
        elif atr_ratio <= 0.02:
            flags.append("low_volatility")

    return _unique_preserve_order(flags)



def _unique_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result



def _build_narrative(states: Dict[str, str]) -> Dict[str, str]:
    return {
        "activity": _activity_narrative(states.get("activity_state", "unknown")),
        "streak": _streak_narrative(states.get("streak_state", "no_clear_streak")),
        "trend": _trend_narrative(states.get("trend_state", "trend_unknown")),
        "momentum": _momentum_narrative(states.get("momentum_state", "unknown")),
        "volatility": _volatility_narrative(states.get("volatility_state", "unknown")),
    }



def _activity_narrative(state: str) -> str:
    mapping = {
        "high_turnover_volume_expanding": "交投活跃，量能同步放大。",
        "high_turnover": "换手偏高，活跃度较强。",
        "low_turnover_volume_contracting": "交投偏淡，量能收缩。",
        "low_turnover": "换手偏低，活跃度不足。",
        "normal_turnover_volume_expanding": "活跃度中性，但量能在放大。",
        "normal_turnover_volume_contracting": "活跃度中性偏弱，量能有收缩迹象。",
        "normal_activity": "活跃度整体中性。",
        "unknown": "活跃度信息不足。",
    }
    return mapping.get(state, "活跃度信息不足。")



def _streak_narrative(state: str) -> str:
    mapping = {
        "extended_up_streak": "连涨时间较长，需警惕短线兑现压力。",
        "short_up_streak": "走势保持一定连续性，短线偏强。",
        "extended_down_streak": "连跌时间较长，短线情绪偏弱。",
        "short_down_streak": "短线仍处于调整节奏中。",
        "no_clear_streak": "连续性不强，走势偏震荡。",
    }
    return mapping.get(state, "连续性信息不足。")



def _trend_narrative(state: str) -> str:
    mapping = {
        "above_ma20_above_ma60": "股价位于MA20和MA60上方，短中期趋势位置偏强。",
        "below_ma20_above_ma60": "股价位于MA20下方但仍在MA60上方，中期结构未完全破坏。",
        "above_ma20_below_ma60": "股价站回MA20上方，但仍在MA60下方，中期压力仍在。",
        "below_ma20_below_ma60": "股价位于MA20和MA60下方，短中期位置偏弱。",
        "trend_unknown": "趋势位置信息不足。",
    }
    return mapping.get(state, "趋势位置信息不足。")



def _momentum_narrative(state: str) -> str:
    mapping = {
        "bullish": "动能偏强，短线强弱结构较好。",
        "bearish": "动能偏弱，反弹确认仍不足。",
        "neutral": "动能中性，方向感不强。",
        "mixed": "动能信号分化，短线判断需结合位置与量能。",
        "unknown": "动能信息不足。",
    }
    return mapping.get(state, "动能信息不足。")



def _volatility_narrative(state: str) -> str:
    mapping = {
        "high_volatility": "波动率偏高，追价需更谨慎。",
        "normal_volatility": "波动率处于正常区间。",
        "low_volatility": "波动率偏低，走势更接近整理。",
        "unknown": "波动率信息不足。",
    }
    return mapping.get(state, "波动率信息不足。")
