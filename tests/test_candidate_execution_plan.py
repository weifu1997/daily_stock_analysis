# -*- coding: utf-8 -*-
"""Tests for L3 candidate execution planning."""

from src.analysis.execution.service import build_execution_plan, build_execution_plan_map
from src.analyzer import AnalysisResult


def test_l3_execution_plan_blocks_below_strong_threshold() -> None:
    plan = build_execution_plan({"score": 17, "trade_bias": "watch", "entry_hint": "等待右侧确认"})

    assert plan["eligible_for_l3"] is False
    assert plan["action"] == "no_trade"
    assert plan["reason_code"] == "l2_not_eligible_for_l3"
    assert plan["hard_stop_loss_pct"] is None


def test_l3_execution_plan_blocks_strong_score_without_right_side_bias() -> None:
    plan = build_execution_plan({"score": 19, "trade_bias": "watch", "entry_hint": "等待右侧确认"})

    assert plan["eligible_for_l3"] is False
    assert plan["action"] == "no_trade"
    assert plan["reason_code"] == "l2_not_right_side_candidate"


def test_l3_execution_plan_creates_watch_entry_plan_for_right_side_candidate() -> None:
    plan = build_execution_plan(
        {
            "score": 19,
            "trade_bias": "right_side_candidate",
            "entry_hint": "等待放量突破后回踩不破",
            "risk_flags": ["20日涨幅偏高"],
        }
    )

    assert plan["eligible_for_l3"] is True
    assert plan["action"] == "watch_for_entry"
    assert plan["entry_condition"] == "等待放量突破后回踩不破"
    assert plan["initial_position_fraction"] == 0.33
    assert plan["max_single_stock_weight"] == 0.10
    assert plan["hard_stop_loss_pct"] == -8
    assert plan["time_stop_days"] == 30
    assert "20日涨幅偏高" in plan["risk_notes"]
    assert any("不自动买入" in note for note in plan["risk_notes"])


def test_l3_execution_plan_map_only_contains_l2_scored_results() -> None:
    allowed = AnalysisResult(code="605305", name="中际联合", sentiment_score=74, trend_prediction="看多", operation_advice="买入")
    allowed.candidate_layer_score = {"score": 19, "trade_bias": "right_side_candidate"}
    missing = AnalysisResult(code="002138", name="顺络电子", sentiment_score=58, trend_prediction="震荡", operation_advice="观望")

    plan_map = build_execution_plan_map([allowed, missing])

    assert sorted(plan_map) == ["605305"]
    assert plan_map["605305"]["eligible_for_l3"] is True
