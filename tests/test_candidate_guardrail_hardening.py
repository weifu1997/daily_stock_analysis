# -*- coding: utf-8 -*-
"""Regression tests for candidate L2/L3 guardrail hardening."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.analysis.execution.service import build_execution_plan, build_execution_plan_map
from src.analysis.normalization import AnalysisNormalizationContext, normalize_analysis_result
from src.analyzer import AnalysisResult
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType


def test_l2_gate_fail_closed_when_candidate_score_missing() -> None:
    result = AnalysisResult(
        code="000682",
        name="东方电子",
        sentiment_score=70,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
    )

    report = normalize_analysis_result(result, AnalysisNormalizationContext(require_candidate_layer_score=True))

    assert result.decision_type == "hold"
    assert result.operation_advice == "持有"
    assert "l2_candidate_score_missing_blocked" in report.reason_codes
    assert result.dashboard["core_conclusion"]["position_advice"]["no_position"] == "L2二筛数据不可用，空仓者不买入，等待评分恢复或右侧确认。"


def test_l2_gate_fail_closed_when_candidate_score_invalid() -> None:
    result = AnalysisResult(
        code="002138",
        name="顺络电子",
        sentiment_score=70,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
    )
    result.candidate_layer_score = {"score": "N/A", "trade_bias": "right_side_candidate"}

    report = normalize_analysis_result(result, AnalysisNormalizationContext(require_candidate_layer_score=True))

    assert result.decision_type == "hold"
    assert result.operation_advice == "持有"
    assert "l2_candidate_score_missing_blocked" in report.reason_codes


def test_l3_execution_plan_map_requires_final_buy_decision() -> None:
    held = AnalysisResult(
        code="605305",
        name="中际联合",
        sentiment_score=74,
        trend_prediction="看多",
        operation_advice="持有",
        decision_type="hold",
        current_price=35.2,
    )
    held.candidate_layer_score = {"score": 19, "trade_bias": "right_side_candidate"}

    plan_map = build_execution_plan_map([held], portfolio_snapshot={"total_cash": 50_000, "total_equity": 200_000})

    assert plan_map == {}


def test_l3_execution_plan_uses_market_lot_size_for_us_stock() -> None:
    plan = build_execution_plan(
        {"score": 19, "trade_bias": "right_side_candidate"},
        portfolio_snapshot={"total_cash": 1_000, "total_equity": 10_000, "accounts": []},
        stock_code="AAPL",
        current_price=180.0,
    )

    assert plan["account_constraints"]["lot_size"] == 1
    assert plan["account_constraints"]["suggested_shares"] == 1


def test_analysis_result_persists_execution_plan_in_dict() -> None:
    result = AnalysisResult(
        code="605305",
        name="中际联合",
        sentiment_score=74,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
    )
    result.execution_plan = {"eligible_for_l3": True, "action": "watch_for_entry"}

    assert result.to_dict()["execution_plan"] == {"eligible_for_l3": True, "action": "watch_for_entry"}


def test_agent_path_applies_candidate_layer_score_before_normalization_gate() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(
        report_language="zh",
        report_integrity_enabled=False,
        agent_skills=None,
    )
    pipeline.social_sentiment_service = None
    pipeline.search_service = None
    pipeline.save_context_snapshot = True
    pipeline.db = MagicMock()
    pipeline._safe_to_dict = StockAnalysisPipeline._safe_to_dict
    pipeline._build_query_context = MagicMock(return_value={})
    pipeline._agent_result_to_analysis_result = MagicMock(
        return_value=AnalysisResult(
            code="002138",
            name="顺络电子",
            sentiment_score=68,
            trend_prediction="看多",
            operation_advice="买入",
            decision_type="buy",
            success=True,
        )
    )
    fake_executor = MagicMock()
    fake_executor.run.return_value = SimpleNamespace(success=True, dashboard={}, provider="test", model="test", error=None)

    candidate_layer_score = {"score": 11, "trade_bias": "watch", "rating": "★★★☆☆ 关注"}
    candidate_source = {"candidate_source": "mx_preselect", "source_rank": 1}

    with patch("src.agent.factory.build_agent_executor", return_value=fake_executor):
        result = pipeline._analyze_with_agent(
            "002138",
            ReportType.SIMPLE,
            "query-agent-l2",
            "顺络电子",
            realtime_quote=None,
            chip_data=None,
            fundamental_context=None,
            trend_result=None,
            portfolio_context=None,
            candidate_layer_score=candidate_layer_score,
            candidate_source=candidate_source,
        )

    assert result.decision_type == "hold"
    assert result.operation_advice == "持有"
    assert result.candidate_layer_score == candidate_layer_score
    assert "l2_candidate_gate_buy_blocked" in result.normalization_report["reason_codes"]
    _, kwargs = fake_executor.run.call_args
    assert kwargs["context"]["candidate_layer_score"] == candidate_layer_score
    assert kwargs["context"]["candidate_source"] == candidate_source
    saved_kwargs = pipeline.db.save_analysis_history.call_args.kwargs
    assert saved_kwargs["context_snapshot"]["candidate_layer_score"] == candidate_layer_score
