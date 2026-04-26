# -*- coding: utf-8 -*-
"""Tests for L3 candidate execution planning."""

from src.analysis.execution.service import build_execution_plan, build_execution_plan_map
from src.analyzer import AnalysisResult


def _strong_candidate(**extra):
    payload = {"score": 19, "trade_bias": "right_side_candidate"}
    payload.update(extra)
    return payload


def test_l3_execution_plan_allows_right_side_candidate_without_score_threshold() -> None:
    plan = build_execution_plan({"score": 17, "trade_bias": "right_side_candidate", "entry_hint": "等待右侧确认"})

    assert plan["eligible_for_l3"] is True
    assert plan["action"] == "watch_for_entry"
    assert plan["hard_stop_loss_pct"] == -8
    assert plan["time_stop_days"] == 30


def test_l3_execution_plan_blocks_strong_score_without_right_side_bias() -> None:
    plan = build_execution_plan({"score": 19, "trade_bias": "watch", "entry_hint": "等待右侧确认"})

    assert plan["eligible_for_l3"] is False
    assert plan["action"] == "no_trade"
    assert plan["reason_code"] == "l2_not_right_side_candidate"


def test_l3_execution_plan_creates_pending_entry_plan_for_right_side_candidate() -> None:
    plan = build_execution_plan(
        _strong_candidate(
            entry_hint="等待放量突破后回踩不破",
            risk_flags=["20日涨幅偏高"],
        )
    )

    assert plan["eligible_for_l3"] is True
    assert plan["action"] == "watch_for_entry"
    assert plan["entry_trigger_status"] == "missing_data"
    assert plan["entry_condition"] == "等待 MACD 金叉、KDJ 低位金叉、布林带收口突破共同确认"
    assert plan["entry_triggers"]["macd_golden_cross"]["status"] == "missing_data"
    assert plan["entry_triggers"]["kdj_low_cross"]["status"] == "missing_data"
    assert plan["entry_triggers"]["bollinger_squeeze_breakout"]["status"] == "missing_data"
    assert plan["entry_triggers"]["volume_confirmed"]["status"] == "missing_data"
    assert plan["initial_position_fraction"] == 0.33
    assert plan["max_single_stock_weight"] == 0.10
    assert plan["hard_stop_loss_pct"] == -8
    assert plan["time_stop_days"] == 30
    assert "20日涨幅偏高" in plan["risk_notes"]
    assert any("不自动买入" in note for note in plan["risk_notes"])


def test_l3_execution_plan_marks_confirmed_when_all_timing_triggers_pass() -> None:
    plan = build_execution_plan(
        _strong_candidate(
            l3_timing={
                "macd_golden_cross": True,
                "kdj_low_cross": True,
                "bollinger_squeeze_breakout": True,
                "volume_confirmed": True,
            }
        )
    )

    assert plan["eligible_for_l3"] is True
    assert plan["action"] == "watch_for_entry"
    assert plan["entry_trigger_status"] == "confirmed"
    assert plan["entry_condition"] == "右侧触发已确认：MACD金叉 + KDJ低位金叉 + 布林带收口突破 + 量能确认"
    assert all(trigger["status"] == "confirmed" for trigger in plan["entry_triggers"].values())


def test_l3_execution_plan_stays_pending_when_only_some_timing_triggers_pass() -> None:
    plan = build_execution_plan(
        _strong_candidate(
            l3_timing={
                "macd_status": "金叉",
                "kdj_status": "低位钝化",
                "bollinger_status": "收口未突破",
                "volume_ratio_20_120": 1.2,
            }
        )
    )

    assert plan["eligible_for_l3"] is True
    assert plan["entry_trigger_status"] == "pending"
    assert plan["entry_triggers"]["macd_golden_cross"]["status"] == "confirmed"
    assert plan["entry_triggers"]["kdj_low_cross"]["status"] == "pending"
    assert plan["entry_triggers"]["bollinger_squeeze_breakout"]["status"] == "pending"
    assert plan["entry_triggers"]["volume_confirmed"]["status"] == "confirmed"
    assert "KDJ低位金叉" in plan["entry_condition"]
    assert "布林带收口突破" in plan["entry_condition"]


def test_l3_execution_plan_accepts_timing_metrics_from_candidate_metrics() -> None:
    plan = build_execution_plan(
        _strong_candidate(
            metrics={
                "macd_status": "MACD金叉",
                "kdj_status": "低位金叉",
                "bollinger_status": "布林带收口突破",
                "volume_ratio_20_120": 1.08,
            }
        )
    )

    assert plan["entry_trigger_status"] == "confirmed"
    assert plan["entry_triggers"]["volume_confirmed"]["status"] == "confirmed"


def test_l3_execution_plan_applies_account_cash_and_lot_constraints_for_new_position() -> None:
    plan = build_execution_plan(
        _strong_candidate(),
        portfolio_snapshot={"total_cash": 50_000, "total_equity": 200_000, "accounts": []},
        stock_code="605305",
        current_price=35.2,
    )

    assert plan["eligible_for_l3"] is True
    assert plan["account_constraints"]["has_position"] is False
    assert plan["account_constraints"]["available_cash"] == 50_000
    assert plan["account_constraints"]["max_position_value"] == 20_000
    assert plan["account_constraints"]["target_entry_value"] == 6_600
    assert plan["account_constraints"]["cash_limited_value"] == 6_600
    assert plan["account_constraints"]["suggested_shares"] == 100
    assert any("按100股一手取整" in note for note in plan["risk_notes"])


def test_l3_execution_plan_respects_existing_position_and_cash_limit() -> None:
    plan = build_execution_plan(
        _strong_candidate(),
        portfolio_snapshot={
            "total_cash": 3_000,
            "total_equity": 100_000,
            "accounts": [
                {
                    "positions": [
                        {
                            "symbol": "605305",
                            "quantity": 200,
                            "market_value_base": 8_000,
                            "last_price": 40.0,
                        }
                    ]
                }
            ],
        },
        stock_code="605305",
        current_price=40.0,
    )

    assert plan["account_constraints"]["has_position"] is True
    assert plan["account_constraints"]["current_position_value"] == 8_000
    assert plan["account_constraints"]["max_additional_value"] == 2_000
    assert plan["account_constraints"]["cash_limited_value"] == 2_000
    assert plan["account_constraints"]["suggested_shares"] == 0
    assert any("不足一手" in note for note in plan["risk_notes"])


def test_l3_execution_plan_map_uses_snapshot_and_result_price() -> None:
    allowed = AnalysisResult(code="605305", name="中际联合", sentiment_score=74, trend_prediction="看多", operation_advice="买入", current_price=35.2)
    allowed.candidate_layer_score = {"score": 19, "trade_bias": "right_side_candidate"}
    missing = AnalysisResult(code="002138", name="顺络电子", sentiment_score=58, trend_prediction="震荡", operation_advice="观望")
    snapshot = {"total_cash": 50_000, "total_equity": 200_000, "accounts": []}

    plan_map = build_execution_plan_map([allowed, missing], portfolio_snapshot=snapshot)

    assert sorted(plan_map) == ["605305"]
    assert plan_map["605305"]["eligible_for_l3"] is True
    assert plan_map["605305"]["account_constraints"]["suggested_shares"] == 100
