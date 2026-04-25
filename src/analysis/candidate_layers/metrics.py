from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from .models import CandidateLayerMetrics


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _enum_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    return str(value)


def _nested_get(data: Any, *paths: str) -> Optional[float]:
    if not isinstance(data, dict):
        return None
    for path in paths:
        cur: Any = data
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            value = _safe_float(cur)
            if value is not None:
                return value
    return None


def _return_pct(series: pd.Series, lookback: int) -> Optional[float]:
    if len(series) <= lookback:
        return None
    prev = _safe_float(series.iloc[-lookback - 1])
    latest = _safe_float(series.iloc[-1])
    if prev is None or latest is None or prev == 0:
        return None
    return (latest / prev - 1.0) * 100.0


def build_candidate_metrics(
    *,
    code: str,
    name: str = "",
    daily_df: Optional[pd.DataFrame],
    trend_result: Any = None,
    fundamental_context: Optional[Dict[str, Any]] = None,
    realtime_quote: Any = None,
) -> CandidateLayerMetrics:
    metrics = CandidateLayerMetrics(code=code, name=name)
    fc = fundamental_context if isinstance(fundamental_context, dict) else {}

    metrics.roe = _nested_get(
        fc,
        "earnings.financial_summary.roe",
        "earnings.data.financial_summary.roe",
        "growth.data.roe",
        "growth.roe",
        "financial_summary.roe",
    )
    metrics.pb = _nested_get(fc, "valuation.pb", "valuation.pb_ratio", "valuation.data.pb", "raw.pb_ratio")
    metrics.pe_ttm = _nested_get(fc, "valuation.pe_ttm", "valuation.data.pe_ttm", "raw.pe_ttm")
    metrics.dividend_yield = _nested_get(
        fc,
        "valuation.dividend_yield",
        "valuation.dividend_yield_ttm",
        "dividend_yield",
        "raw.dividend_yield",
    )

    if realtime_quote is not None:
        metrics.pb = metrics.pb if metrics.pb is not None else _safe_float(getattr(realtime_quote, "pb_ratio", None))
        metrics.pe_ttm = metrics.pe_ttm if metrics.pe_ttm is not None else _safe_float(getattr(realtime_quote, "pe_ttm", None))
        if metrics.pe_ttm is None:
            metrics.pe_ttm = _safe_float(getattr(realtime_quote, "pe_ratio", None))

    if daily_df is None or daily_df.empty or "close" not in daily_df.columns:
        metrics.data_warnings.append("数据不足")
        _attach_trend_metrics(metrics, trend_result)
        return metrics

    df = daily_df.copy().sort_values("date") if "date" in daily_df.columns else daily_df.copy()
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    if close.empty:
        metrics.data_warnings.append("收盘价缺失")
        _attach_trend_metrics(metrics, trend_result)
        return metrics

    metrics.close = _safe_float(close.iloc[-1])
    metrics.return_20d = _return_pct(close, 20)
    metrics.return_60d = _return_pct(close, 60)
    metrics.return_120d = _return_pct(close, 120)

    if len(close) >= 120:
        window_120 = close.iloc[-120:]
        low_120 = _safe_float(window_120.min())
        high_120 = _safe_float(window_120.max())
        latest = metrics.close
        if low_120 is not None and high_120 is not None and low_120 > 0:
            metrics.range_120d = (high_120 / low_120 - 1.0) * 100.0
            if latest is not None and high_120 != low_120:
                metrics.position_120d = (latest - low_120) / (high_120 - low_120) * 100.0
    else:
        metrics.data_warnings.append("120日数据不足")

    if len(close) >= 20:
        returns_20 = close.pct_change().dropna().iloc[-20:]
        if not returns_20.empty:
            metrics.volatility_20d = float(returns_20.std() * 100.0)
        metrics.ma20 = float(close.iloc[-20:].mean())
        if len(close) >= 25:
            prev_ma20 = float(close.iloc[-25:-5].mean())
            metrics.ma20_up = metrics.ma20 > prev_ma20
    if len(close) >= 60:
        metrics.ma60 = float(close.iloc[-60:].mean())
    if metrics.close is not None and metrics.ma20 is not None and metrics.ma60 is not None:
        metrics.ma_bullish = metrics.close > metrics.ma20 > metrics.ma60

    amount_col = "amount" if "amount" in df.columns else "成交额" if "成交额" in df.columns else None
    if amount_col and len(df) >= 120:
        amount = pd.to_numeric(df[amount_col], errors="coerce").dropna()
        if len(amount) >= 120:
            avg20 = float(amount.iloc[-20:].mean())
            avg120 = float(amount.iloc[-120:].mean())
            if avg120 > 0:
                metrics.volume_ratio_20_120 = avg20 / avg120
    elif len(df) < 120:
        pass
    else:
        metrics.data_warnings.append("成交额缺失")

    _attach_trend_metrics(metrics, trend_result)
    return metrics


def _attach_trend_metrics(metrics: CandidateLayerMetrics, trend_result: Any) -> None:
    if trend_result is None:
        return
    trend_ma20 = _safe_float(getattr(trend_result, "ma20", None))
    trend_ma60 = _safe_float(getattr(trend_result, "ma60", None))
    trend_price = _safe_float(getattr(trend_result, "current_price", None))
    if trend_ma20 is not None:
        metrics.ma20 = trend_ma20
    if trend_ma60 is not None:
        metrics.ma60 = trend_ma60
    if metrics.close is None and trend_price is not None:
        metrics.close = trend_price
    if metrics.close is not None and metrics.ma20 is not None and metrics.ma60 is not None:
        metrics.ma_bullish = metrics.close > metrics.ma20 > metrics.ma60
    metrics.macd_status = _enum_value(getattr(trend_result, "macd_status", None))
    metrics.trend_status = _enum_value(getattr(trend_result, "trend_status", None))
    metrics.trend_signal_score = _safe_float(getattr(trend_result, "signal_score", None))
