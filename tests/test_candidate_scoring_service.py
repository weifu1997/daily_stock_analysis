import pandas as pd

from src.services.candidate_scoring_service import CandidateScoringService


def _df_from_closes(closes, amount_start=1_000_000):
    dates = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "date": dates.astype(str),
            "close": closes,
            "amount": [amount_start + i * 20_000 for i in range(len(closes))],
            "volume": [1000 + i for i in range(len(closes))],
        }
    )


def test_score_candidate_recommends_low_valuation_dividend_with_right_side_features():
    closes = [10 + i * 0.01 for i in range(130)]
    service = CandidateScoringService()

    result = service.score_candidate(
        code="601298.SH",
        name="青岛港",
        daily_df=_df_from_closes(closes),
        trend_result=None,
        fundamental_context={
            "earnings": {"financial_summary": {"roe": 11.95}},
            "valuation": {"pb": 1.28, "pe_ttm": 11.16, "dividend_yield": 4.14},
        },
    )

    assert result.code == "601298.SH"
    assert result.score >= 14
    assert "推荐" in result.rating or "关注" in result.rating
    assert result.excluded is False
    assert result.core_logic
    assert result.metrics["roe"] == 11.95


def test_score_candidate_downgrades_low_roe_even_when_technical_shape_is_good():
    closes = [10 + i * 0.02 for i in range(130)]
    service = CandidateScoringService()

    result = service.score_candidate(
        code="600639.SH",
        name="浦东金桥",
        daily_df=_df_from_closes(closes),
        trend_result=None,
        fundamental_context={
            "earnings": {"financial_summary": {"roe": 7.3}},
            "valuation": {"pb": 0.82, "pe_ttm": 11.63, "dividend_yield": 3.89},
        },
    )

    assert result.score < 18
    assert "ROE低于8%" in "；".join(result.risk_flags)
    assert "强烈推荐" not in result.rating


def test_score_candidate_marks_high_position_as_risk_not_buy_signal():
    closes = [10] * 100 + [10 + i * 0.1 for i in range(30)]
    service = CandidateScoringService()

    result = service.score_candidate(
        code="600461.SH",
        name="洪城环境",
        daily_df=_df_from_closes(closes),
        trend_result=None,
        fundamental_context={
            "earnings": {"financial_summary": {"roe": 12.44}},
            "valuation": {"pb": 1.33, "pe_ttm": 11.08, "dividend_yield": 5.04},
        },
    )

    assert any("120日位置偏高" in flag for flag in result.risk_flags)
    assert result.trade_bias == "watch"


def test_score_candidate_fail_open_on_missing_data():
    service = CandidateScoringService()

    result = service.score_candidate(
        code="000000.SH",
        name="缺数据",
        daily_df=None,
        trend_result=None,
        fundamental_context=None,
    )

    assert result.code == "000000.SH"
    assert result.excluded is False
    assert result.trade_bias == "watch"
    assert "数据不足" in "；".join(result.risk_flags)
