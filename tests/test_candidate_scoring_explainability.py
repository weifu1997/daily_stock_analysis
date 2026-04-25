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


def test_score_candidate_exposes_human_readable_factor_breakdown():
    service = CandidateScoringService()

    result = service.score_candidate(
        code="601298.SH",
        name="青岛港",
        daily_df=_df_from_closes([10 + i * 0.01 for i in range(130)]),
        trend_result=None,
        fundamental_context={
            "earnings": {"financial_summary": {"roe": 11.95}},
            "valuation": {"pb": 1.28, "pe_ttm": 11.16, "dividend_yield": 4.14},
        },
    )

    payload = result.to_dict()

    assert "factor_breakdown" in payload
    assert [item["key"] for item in payload["factor_breakdown"]] == [
        "valuation",
        "quality",
        "position",
        "technical",
        "volume",
    ]
    assert all("label" in item for item in payload["factor_breakdown"])
    assert all("score" in item for item in payload["factor_breakdown"])
    assert all("note" in item for item in payload["factor_breakdown"])
    assert payload["factor_breakdown"][0]["label"] == "估值"
    assert "PB" in payload["factor_breakdown"][0]["note"]


def test_score_candidate_exposes_no_trade_reason_without_changing_trade_bias():
    service = CandidateScoringService()

    result = service.score_candidate(
        code="600639.SH",
        name="浦东金桥",
        daily_df=_df_from_closes([10 + i * 0.02 for i in range(130)]),
        trend_result=None,
        fundamental_context={
            "earnings": {"financial_summary": {"roe": 7.3}},
            "valuation": {"pb": 0.82, "pe_ttm": 11.63, "dividend_yield": 3.89},
        },
    )

    payload = result.to_dict()

    assert payload["trade_bias"] == "watch"
    assert payload["no_trade_reason"]
    assert "ROE低于8%" in payload["no_trade_reason"]
    assert payload["entry_hint"]
    assert "右侧" in payload["entry_hint"] or "观察" in payload["entry_hint"]
