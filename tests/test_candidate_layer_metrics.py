import math
from types import SimpleNamespace

import pandas as pd

from src.analysis.candidate_layers.metrics import build_candidate_metrics


def _daily_frame(days=130):
    dates = pd.date_range("2025-01-01", periods=days, freq="D")
    close = [10 + i * 0.02 for i in range(days)]
    volume = [1000 + i for i in range(days)]
    amount = [1_000_000 + i * 10_000 for i in range(days)]
    return pd.DataFrame(
        {
            "date": dates.astype(str),
            "close": close,
            "volume": volume,
            "amount": amount,
        }
    )


def test_build_candidate_metrics_computes_window_returns_position_and_volume_ratio():
    df = _daily_frame()
    metrics = build_candidate_metrics(
        code="601298.SH",
        name="青岛港",
        daily_df=df,
        trend_result=None,
        fundamental_context={"earnings": {"financial_summary": {"roe": 11.95}}},
    )

    assert metrics.code == "601298.SH"
    assert metrics.name == "青岛港"
    assert metrics.close == df.iloc[-1]["close"]
    assert metrics.return_20d is not None
    assert metrics.return_60d is not None
    assert metrics.return_120d is not None
    assert metrics.range_120d is not None
    assert metrics.position_120d is not None
    assert 0 <= metrics.position_120d <= 100
    assert metrics.volume_ratio_20_120 is not None
    assert metrics.ma20 is not None
    assert metrics.ma60 is not None
    assert metrics.ma20_up is True


def test_build_candidate_metrics_fail_open_when_daily_data_is_short():
    metrics = build_candidate_metrics(
        code="600000.SH",
        name="短数据",
        daily_df=_daily_frame(days=10),
        trend_result=None,
        fundamental_context={},
    )

    assert metrics.code == "600000.SH"
    assert metrics.return_120d is None
    assert metrics.position_120d is None
    assert metrics.volume_ratio_20_120 is None


def test_build_candidate_metrics_reuses_trend_result_for_ma_and_macd():
    trend = SimpleNamespace(
        ma20=10.5,
        ma60=10.1,
        current_price=10.8,
        macd_status=SimpleNamespace(value="多头"),
        trend_status=SimpleNamespace(value="多头排列"),
        signal_score=72,
    )

    metrics = build_candidate_metrics(
        code="600461.SH",
        name="洪城环境",
        daily_df=_daily_frame(),
        trend_result=trend,
        fundamental_context={},
    )

    assert metrics.ma20 == 10.5
    assert metrics.ma60 == 10.1
    assert metrics.ma_bullish is True
    assert metrics.macd_status == "多头"
    assert metrics.trend_signal_score == 72
