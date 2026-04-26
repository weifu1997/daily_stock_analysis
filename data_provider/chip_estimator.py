# -*- coding: utf-8 -*-
"""
===================================
估算筹码分布（OHLCV Proxy）
===================================

用途：
- 当真实筹码源（Tushare / AkShare）不可用时，提供一个保守的估算筹码分布。
- 只能作为辅助信号，不应伪装为真实筹码。

设计原则：
- 以最近 N 天 OHLCV 为输入
- 用“截断高斯核”近似每日成本分布
- 用时间衰减、换手率衰减、振幅惩罚共同控制权重
- 输出必须带 source / method / confidence

注意：
- 这是 proxy，不是真实筹码。
- 低置信度时应在上层报告中明确提示“仅作辅助”。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

import logging

logger = logging.getLogger(__name__)


@dataclass
class EstimatedChipDistribution:
    code: str
    date: str = ""
    source: str = "estimated_ohlcv"
    method: str = "truncated_gaussian"
    confidence: float = 0.0
    sample_days: int = 0
    window_days: int = 120

    profit_ratio: float = 0.0
    avg_cost: float = 0.0
    cost_90_low: float = 0.0
    cost_90_high: float = 0.0
    concentration_90: float = 0.0
    cost_70_low: float = 0.0
    cost_70_high: float = 0.0
    concentration_70: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "date": self.date,
            "source": self.source,
            "method": self.method,
            "confidence": self.confidence,
            "sample_days": self.sample_days,
            "window_days": self.window_days,
            "profit_ratio": self.profit_ratio,
            "avg_cost": self.avg_cost,
            "cost_90_low": self.cost_90_low,
            "cost_90_high": self.cost_90_high,
            "concentration_90": self.concentration_90,
            "cost_70_low": self.cost_70_low,
            "cost_70_high": self.cost_70_high,
            "concentration_70": self.concentration_70,
        }

    def get_chip_status(self, current_price: float) -> str:
        """Compatibility shim aligned with legacy ChipDistribution.get_chip_status().

        Returns a conservative human-readable status string. Low-confidence estimates
        are explicitly downgraded so downstream reports do not treat them as strong signals.
        """
        confidence = _safe_float(self.confidence, default=0.0) or 0.0
        current_price = _safe_float(current_price, default=0.0) or 0.0
        avg_cost = _safe_float(self.avg_cost, default=0.0) or 0.0
        profit_ratio = float(np.clip(_safe_float(self.profit_ratio, default=0.0) or 0.0, 0.0, 1.0))
        concentration_90 = float(max(_safe_float(self.concentration_90, default=0.0) or 0.0, 0.0))

        if confidence >= 0.75:
            confidence_label = "高置信度"
            confidence_prefix = "估算筹码"
        elif confidence >= 0.50:
            confidence_label = "中置信度"
            confidence_prefix = "估算筹码"
        else:
            confidence_label = "低置信度(降级)"
            confidence_prefix = "估算筹码"

        status_parts = [f"{confidence_prefix}{confidence_label}"]

        # 获利/套牢盘状态，保持与旧版输出风格一致，但对低置信度做显式降级提示
        if profit_ratio >= 0.9:
            status_parts.append("获利盘极高(获利盘>90%)")
        elif profit_ratio >= 0.7:
            status_parts.append("获利盘较高(获利盘70-90%)")
        elif profit_ratio >= 0.5:
            status_parts.append("获利盘中等(获利盘50-70%)")
        elif profit_ratio >= 0.3:
            status_parts.append("套牢盘中等(套牢盘50-70%)")
        elif profit_ratio >= 0.1:
            status_parts.append("套牢盘较高(套牢盘70-90%)")
        else:
            status_parts.append("套牢盘极高(套牢盘>90%)")

        # 筹码集中度分析（越小越集中）
        if concentration_90 < 0.08:
            status_parts.append("筹码高度集中")
        elif concentration_90 < 0.15:
            status_parts.append("筹码较集中")
        elif concentration_90 < 0.25:
            status_parts.append("筹码分散度中等")
        else:
            status_parts.append("筹码较分散")

        # 成本与现价关系：低置信度时保留，但会被前缀明确降级
        if current_price > 0 and avg_cost > 0:
            cost_diff = (current_price - avg_cost) / avg_cost * 100
            if cost_diff > 20:
                status_parts.append(f"现价高于平均成本{cost_diff:.1f}%")
            elif cost_diff > 5:
                status_parts.append(f"现价略高于成本{cost_diff:.1f}%")
            elif cost_diff > -5:
                status_parts.append("现价接近平均成本")
            else:
                status_parts.append(f"现价低于平均成本{abs(cost_diff):.1f}%")

        # 兜底说明：低置信度明确降级，不返回误导性强结论
        if confidence < 0.50:
            status_parts.append("仅作辅助参考")
        elif confidence < 0.75:
            status_parts.append("建议结合其他信号确认")

        return "，".join(status_parts)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            s = v.strip()
            if s in ("", "-", "--", "N/A", "n/a"):
                return default
        x = float(v)
        if np.isnan(x):
            return default
        return x
    except Exception:
        logger.warning("Broad exception caught", exc_info=True)
        return default


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if len(values) == 0 or len(values) != len(weights):
        return 0.0
    total_w = weights.sum()
    if total_w <= 0:
        return 0.0
    sorter = np.argsort(values)
    values = values[sorter]
    weights = weights[sorter]
    cdf = np.cumsum(weights) / total_w
    return float(np.interp(q, cdf, values))


def _build_confidence(df: pd.DataFrame, window: int) -> float:
    """
    置信度：覆盖度 + 稳定性 + 流动性 + 新鲜度
    取值 0~1，偏保守。
    """
    if df is None or df.empty:
        return 0.0

    n = len(df)
    coverage = min(n / float(window), 1.0)

    # 稳定性：近端日内振幅越剧烈，置信度越低
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    amp = ((high - low) / close.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).dropna()
    if len(amp) == 0:
        stability = 0.5
    else:
        amp_med = float(np.nanmedian(amp))
        # 0.05 附近较稳，0.15 以上明显不稳
        stability = float(np.clip(1.0 - amp_med / 0.15, 0.0, 1.0))

    # 流动性：相对成交量越大越好
    vol = df["volume"].astype(float)
    vol_med = float(np.nanmedian(vol)) if len(vol) > 0 else 0.0
    vol_p20 = float(np.nanpercentile(vol, 20)) if len(vol) > 0 else 0.0
    if vol_med <= 0:
        liquidity = 0.0
    else:
        liquidity = float(np.clip((vol_p20 / vol_med), 0.0, 1.0))

    # 新鲜度：最后一条越接近最新交易日越好（这里默认只看输入窗口，给保守值）
    freshness = 1.0 if n >= 5 else 0.5

    conf = 0.30 * coverage + 0.25 * stability + 0.25 * liquidity + 0.20 * freshness
    return float(np.clip(conf, 0.0, 1.0))


def estimate_chip_distribution(
    daily_df: pd.DataFrame,
    stock_code: str,
    current_price: float,
    window: int = 120,
    bins: int = 240,
) -> Optional[EstimatedChipDistribution]:
    """
    用 OHLCV 估算筹码分布。

    参数：
    - daily_df: 至少包含 high/low/close/volume，建议可选包含 turnover_rate
    - current_price: 当前价格，用于计算获利比例
    - window: 取最近多少天
    - bins: 价格离散粒度
    """
    if daily_df is None or daily_df.empty:
        return None

    need_cols = ["high", "low", "close", "volume"]
    for col in need_cols:
        if col not in daily_df.columns:
            return None

    df = daily_df.tail(window).copy()
    df = df.dropna(subset=need_cols)
    if df.empty:
        return None

    # 统一列类型
    for col in need_cols:
        df[col] = df[col].map(_safe_float)

    if "turnover_rate" in df.columns:
        df["turnover_rate"] = df["turnover_rate"].map(_safe_float)

    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    if not np.isfinite(price_min) or not np.isfinite(price_max) or price_max <= price_min:
        return None

    grid = np.linspace(price_min, price_max, bins)
    hist = np.zeros_like(grid, dtype=float)

    n = len(df)
    tau = max(min(window / 2.0, 45.0), 20.0)  # 保守一点，避免太快衰减

    for idx, row in enumerate(df.itertuples(index=False)):
        # 兼容命名列 / 元组访问
        row_dict = row._asdict() if hasattr(row, "_asdict") else dict(row)
        high = _safe_float(row_dict.get("high"))
        low = _safe_float(row_dict.get("low"))
        close = _safe_float(row_dict.get("close"))
        volume = _safe_float(row_dict.get("volume"))
        if high <= 0 or low <= 0 or close <= 0 or volume <= 0 or high < low:
            continue

        # 中心价偏向收盘价，避免过度依赖高低点
        mu = (high + low + 2.0 * close) / 4.0

        # 波动越大，核越宽；但设置下限，防止过窄
        range_width = max(high - low, close * 0.005)
        sigma = max(range_width / 6.0, close * 0.002)

        # 时间衰减：越新权重越高
        age = (n - 1) - idx
        recency_weight = float(np.exp(-age / tau))

        # 振幅惩罚：大振幅日权重下调
        amplitude = (high - low) / close
        range_penalty = float(1.0 / (1.0 + amplitude * 2.0))

        # 换手率衰减：有数据就用，没有就不加惩罚
        turnover_rate = row_dict.get("turnover_rate", None)
        if turnover_rate is None:
            turnover_weight = 1.0
        else:
            tr = _safe_float(turnover_rate, default=0.0)
            turnover_weight = float(np.clip(1.0 - tr / 100.0, 0.1, 1.0))

        day_weight = volume * recency_weight * range_penalty * turnover_weight

        # 截断高斯核：只在 [low, high] 区间内归一
        density = np.exp(-0.5 * ((grid - mu) / sigma) ** 2)
        density[(grid < low) | (grid > high)] = 0.0
        density_sum = float(density.sum())
        if density_sum <= 0:
            continue
        density /= density_sum
        hist += day_weight * density

    total = float(hist.sum())
    if total <= 0:
        return None
    hist /= total

    avg_cost = float((grid * hist).sum())
    if not np.isfinite(avg_cost) or avg_cost <= 0:
        return None

    current_price = _safe_float(current_price, default=0.0)
    profit_ratio = float(hist[grid <= current_price].sum()) if current_price > 0 else 0.0
    profit_ratio = float(np.clip(profit_ratio, 0.0, 1.0))

    q05 = _weighted_quantile(grid, hist, 0.05)
    q15 = _weighted_quantile(grid, hist, 0.15)
    q85 = _weighted_quantile(grid, hist, 0.85)
    q95 = _weighted_quantile(grid, hist, 0.95)

    concentration_90 = float((q95 - q05) / avg_cost) if avg_cost > 0 else 0.0
    concentration_70 = float((q85 - q15) / avg_cost) if avg_cost > 0 else 0.0

    confidence = _build_confidence(df, window)

    # 过低质量时不强行输出“像真的”结果，仍可返回但置信度会很低
    latest_date = ""
    if "date" in df.columns and len(df) > 0:
        latest_raw = df.iloc[-1]["date"]
        if pd.notna(latest_raw):
            latest_date = pd.to_datetime(latest_raw).strftime("%Y-%m-%d")

    return EstimatedChipDistribution(
        code=stock_code,
        date=latest_date,
        source="estimated_ohlcv",
        method="truncated_gaussian",
        confidence=confidence,
        sample_days=int(len(df)),
        window_days=int(window),
        profit_ratio=profit_ratio,
        avg_cost=avg_cost,
        cost_90_low=float(q05),
        cost_90_high=float(q95),
        concentration_90=concentration_90,
        cost_70_low=float(q15),
        cost_70_high=float(q85),
        concentration_70=concentration_70,
    )
