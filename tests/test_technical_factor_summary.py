from src.analysis.technical_factor_summary import summarize_stk_factor_snapshot


class TestTechnicalFactorSummary:
    def test_summary_returns_none_for_empty_input(self):
        assert summarize_stk_factor_snapshot({}) is None
        assert summarize_stk_factor_snapshot(None) is None

    def test_summary_builds_expected_states_for_bearish_case(self):
        snapshot = {
            "trade_date": "20260417",
            "turnover_rate": 2.3,
            "volume_ratio": 0.75,
            "updays": 0,
            "downdays": 3,
            "ma_qfq_20": 11.20,
            "ma_qfq_60": 10.80,
            "macd_qfq": -0.12,
            "rsi_qfq_12": 42.6,
            "boll_mid_qfq": 11.10,
            "atr_qfq": 0.24,
        }

        result = summarize_stk_factor_snapshot(snapshot, close_price=11.01)

        assert result is not None
        assert result["states"]["activity_state"] == "normal_turnover_volume_contracting"
        assert result["states"]["streak_state"] == "short_down_streak"
        assert result["states"]["trend_state"] == "below_ma20_above_ma60"
        assert result["states"]["momentum_state"] == "bearish"
        assert result["states"]["volatility_state"] == "normal_volatility"

    def test_summary_generates_flags_consistently(self):
        snapshot = {
            "trade_date": "20260417",
            "turnover_rate": 8.5,
            "volume_ratio": 1.7,
            "updays": 3,
            "downdays": 0,
            "ma_qfq_20": 10.5,
            "ma_qfq_60": 9.9,
            "macd_qfq": 0.2,
            "rsi_qfq_12": 72.0,
            "boll_mid_qfq": 10.4,
            "atr_qfq": 0.18,
        }

        result = summarize_stk_factor_snapshot(snapshot, close_price=10.95)

        assert result is not None
        flags = set(result["flags"])
        assert "volume_expanding" in flags
        assert "high_turnover" in flags
        assert "multi_day_up" in flags
        assert "above_ma20" in flags
        assert "above_ma60" in flags
        assert "macd_positive" in flags
        assert "rsi_overbought" in flags
        assert "above_boll_mid" in flags

    def test_summary_builds_narrative_from_states(self):
        snapshot = {
            "trade_date": "20260417",
            "turnover_rate": 9.2,
            "volume_ratio": 1.8,
            "updays": 4,
            "downdays": 0,
            "ma_qfq_20": 10.50,
            "ma_qfq_60": 9.80,
            "macd_qfq": 0.21,
            "rsi_qfq_12": 63.0,
            "boll_mid_qfq": 10.40,
            "atr_qfq": 0.20,
        }

        result = summarize_stk_factor_snapshot(snapshot, close_price=10.95)

        assert result is not None
        assert "活跃" in result["narrative"]["activity"] or "换手" in result["narrative"]["activity"]
        assert "趋势" in result["narrative"]["trend"] or "MA20" in result["narrative"]["trend"]
        assert "动能" in result["narrative"]["momentum"]

    def test_summary_uses_passed_close_price_over_snapshot_close(self):
        snapshot = {
            "trade_date": "20260417",
            "close": 99.0,
            "ma_qfq_20": 11.20,
            "ma_qfq_60": 10.80,
            "macd_qfq": -0.12,
            "rsi_qfq_12": 42.6,
            "boll_mid_qfq": 11.10,
            "atr_qfq": 0.24,
        }

        result = summarize_stk_factor_snapshot(snapshot, close_price=11.01)

        assert result is not None
        assert result["close_price"] == 11.01
        assert result["states"]["trend_state"] == "below_ma20_above_ma60"

    def test_summary_handles_missing_partial_metrics_fail_open(self):
        snapshot = {
            "trade_date": "20260417",
            "turnover_rate": None,
            "volume_ratio": None,
            "ma_qfq_20": None,
            "ma_qfq_60": None,
        }

        result = summarize_stk_factor_snapshot(snapshot, close_price=11.01)

        assert result is not None
        assert result["states"]["activity_state"] == "unknown"
        assert result["states"]["trend_state"] == "trend_unknown"
