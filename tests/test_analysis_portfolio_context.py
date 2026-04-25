# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.analysis.context_models import PortfolioContext
from src.analysis.normalization import AnalysisNormalizationContext, normalize_analysis_result
from src.analysis.normalization.service import build_default_rule_chain
from src.analysis.result_normalizer import normalize_analysis_result_for_portfolio_context
from src.analyzer import AnalysisResult, GeminiAnalyzer
from src.core.pipeline import StockAnalysisPipeline


class _DummyConfig(SimpleNamespace):
    gemini_request_delay = 0
    report_language = "zh"


def test_portfolio_context_to_dict_roundtrip() -> None:
    context = PortfolioContext(
        has_position=True,
        quantity=200.0,
        cost_basis=12.34,
        unrealized_pnl=345.67,
        valuation_currency="CNY",
    )

    assert context.to_dict() == {
        "has_position": True,
        "quantity": 200.0,
        "cost_basis": 12.34,
        "unrealized_pnl": 345.67,
        "valuation_currency": "CNY",
        "source": "portfolio_snapshot",
    }


def test_pipeline_build_portfolio_context_for_stock_from_snapshot() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline._portfolio_snapshot_loaded = True
    pipeline._portfolio_snapshot_cache = {
        "accounts": [
            {
                "base_currency": "CNY",
                "positions": [
                    {
                        "symbol": "600519",
                        "quantity": 100.0,
                        "avg_cost": 1500.0,
                        "unrealized_pnl_base": 888.0,
                        "valuation_currency": "CNY",
                    }
                ],
            }
        ]
    }

    portfolio_context = pipeline._build_portfolio_context_for_stock("600519")

    assert portfolio_context.has_position is True
    assert portfolio_context.quantity == 100.0
    assert portfolio_context.cost_basis == 1500.0
    assert portfolio_context.unrealized_pnl == 888.0
    assert portfolio_context.valuation_currency == "CNY"


def test_pipeline_build_portfolio_context_marks_absent_position() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline._portfolio_snapshot_loaded = True
    pipeline._portfolio_snapshot_cache = {"accounts": [{"positions": []}]}

    portfolio_context = pipeline._build_portfolio_context_for_stock("600519")

    assert portfolio_context.has_position is False
    assert portfolio_context.source == "portfolio_snapshot"


def test_enhance_context_includes_explicit_portfolio_context_dict() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(report_language="zh")
    pipeline.search_service = SimpleNamespace(news_window_days=3)
    pipeline.fetcher_manager = SimpleNamespace(
        build_failed_fundamental_context=lambda code, reason: {
            "status": "failed",
            "code": code,
            "reason": reason,
        }
    )

    enhanced = pipeline._enhance_context(
        context={"code": "600519", "date": "2026-01-06"},
        realtime_quote=None,
        chip_data=None,
        trend_result=None,
        stock_name="贵州茅台",
        fundamental_context=None,
        portfolio_context=PortfolioContext(
            has_position=True,
            quantity=50.0,
            cost_basis=1400.0,
            unrealized_pnl=200.0,
            valuation_currency="CNY",
        ),
    )

    assert enhanced["portfolio_context"]["has_position"] is True
    assert enhanced["portfolio_context"]["quantity"] == 50.0
    assert enhanced["portfolio_context"]["cost_basis"] == 1400.0


def test_analyzer_prompt_prioritizes_fundamentals_over_technicals() -> None:
    config = _DummyConfig()
    with patch.object(GeminiAnalyzer, "_init_litellm", lambda self: setattr(self, "_litellm_available", False)):
        analyzer = GeminiAnalyzer(
            config=config,
            skill_instructions="",
            default_skill_policy="",
            use_legacy_default_prompt=False,
        )

    prompt = analyzer._get_analysis_system_prompt("zh", "600519")

    assert "A 股投资分析师" in prompt
    assert "decision_type" in prompt
    assert "输出语言（最高优先级）" in prompt


def test_portfolio_advice_guardrails_rewrite_non_position_operation_advice() -> None:
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=70,
        trend_prediction="看多",
        operation_advice="加仓",
        decision_type="hold",
        dashboard={
            "core_conclusion": {
                "position_advice": {
                    "no_position": "空仓者可考虑加仓等待回踩",
                    "has_position": "持仓者可以继续加仓",
                }
            }
        },
    )

    normalize_analysis_result_for_portfolio_context(
        result,
        PortfolioContext(has_position=False),
    )

    assert result.operation_advice == "买入"
    assert result.decision_type == "buy"
    assert result.dashboard["core_conclusion"]["position_advice"]["no_position"] == "空仓者可考虑买入等待回踩"


def test_portfolio_advice_guardrails_keep_position_adjustment_when_holding() -> None:
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=70,
        trend_prediction="看多",
        operation_advice="减仓",
        decision_type="sell",
    )

    normalize_analysis_result_for_portfolio_context(
        result,
        PortfolioContext(has_position=True),
    )

    assert result.operation_advice == "减仓"
    assert result.decision_type == "sell"


def test_normalization_subsystem_fills_missing_operation_advice_from_decision_type() -> None:
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=60,
        trend_prediction="震荡",
        operation_advice="",
        decision_type="BUY",
    )

    normalize_analysis_result(
        result,
        AnalysisNormalizationContext(portfolio_context=PortfolioContext(has_position=False)),
    )

    assert result.decision_type == "buy"
    assert result.operation_advice == "买入"


def test_normalization_risk_penalty_high_buy_is_hard_vetoed() -> None:
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=78,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
    )
    result.risk_penalty = 0.82

    report = normalize_analysis_result(result, AnalysisNormalizationContext())

    assert result.decision_type == "hold"
    assert result.operation_advice == "持有"
    assert report.max_severity == "hard_guardrail"
    assert "risk_penalty" in " ".join(report.reason_codes)
    assert any(
        record.severity == "hard_guardrail" and "decision_type" in record.modified_fields
        for record in report.applied_rules
    )


def test_normalization_sell_signals_standardize_to_sell_action() -> None:
    result = AnalysisResult(
        code="002138",
        name="顺络电子",
        sentiment_score=32,
        trend_prediction="看空",
        operation_advice="减仓",
        decision_type="sell",
    )
    result.risk_penalty = 0.86

    report = normalize_analysis_result(result, AnalysisNormalizationContext())

    assert result.decision_type == "sell"
    assert result.operation_advice == "卖出"
    assert report.max_severity == "hard_guardrail"
    assert "portfolio_non_holder_action_adjusted" in " ".join(report.reason_codes)
    assert any(
        record.severity == "hard_guardrail" and "operation_advice" in record.modified_fields
        for record in report.applied_rules
    )


def test_normalization_rule_chain_supports_custom_registered_rules() -> None:
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=60,
        trend_prediction="震荡",
        operation_advice="",
        decision_type="hold",
    )
    applied = []

    class MarkerRule:
        name = "marker"

        def apply(self, result, context) -> None:
            applied.append((result.code, context.portfolio_context.has_position))
            result.analysis_summary = "normalized-by-marker"

    rule_chain = build_default_rule_chain(extra_rules=[MarkerRule()])
    rule_chain.apply(result, AnalysisNormalizationContext(portfolio_context=PortfolioContext(has_position=False)))

    assert applied == [("600519", False)]
    assert result.analysis_summary == "normalized-by-marker"


def test_normalization_rule_chain_rejects_duplicate_rule_names() -> None:
    class DuplicateRule:
        name = "decision-consistency"

        def apply(self, result, context) -> None:
            return None

    with pytest.raises(ValueError, match="duplicate normalization rule"):
        build_default_rule_chain(extra_rules=[DuplicateRule()])


def test_normalization_rule_chain_returns_hit_records_and_modified_fields() -> None:
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=70,
        trend_prediction="看多",
        operation_advice="加仓",
        decision_type="hold",
        dashboard={
            "core_conclusion": {
                "position_advice": {
                    "no_position": "空仓者可考虑加仓等待回踩",
                }
            }
        },
    )

    report = build_default_rule_chain().apply(
        result,
        AnalysisNormalizationContext(portfolio_context=PortfolioContext(has_position=False)),
    )

    assert report.applied_rules
    assert any(record.rule_name == "portfolio-context" and record.changed for record in report.applied_rules)
    portfolio_record = next(record for record in report.applied_rules if record.rule_name == "portfolio-context")
    assert portfolio_record.severity == "hard_guardrail"
    assert portfolio_record.reason_code == "portfolio_non_holder_action_adjusted"
    assert "operation_advice" in portfolio_record.modified_fields
    assert "decision_type" in portfolio_record.modified_fields
    assert "operation_advice" in portfolio_record.modified_fields
    assert any("dashboard.core_conclusion.position_advice.no_position" == field for field in portfolio_record.modified_fields)
    assert "dashboard.core_conclusion.position_advice.no_position" in portfolio_record.field_transitions

def test_normalization_guardrail_downgrades_buy_when_holder_structure_is_distributed_and_risks_clustered() -> None:
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=82,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
        dashboard={
            "core_conclusion": {
                "one_sentence": "筹码结构虽然分散，但可以积极买入。",
                "position_advice": {
                    "no_position": "空仓者可积极买入布局",
                    "has_position": "持仓者可继续持有观察",
                },
            },
            "intelligence": {
                "risk_alerts": ["大股东减持", "订单不及预期"],
                "positive_catalysts": ["新品发布"],
                "latest_news": "2026-04-16 公司公告",
            },
            "data_perspective": {
                "institution_structure": {
                    "holder_structure_bias": "分散",
                    "holder_structure_note": "前十大净减持 + 户数上升，筹码扩散。",
                }
            },
        },
    )

    report = normalize_analysis_result(
        result,
        AnalysisNormalizationContext(portfolio_context=PortfolioContext(has_position=False)),
    )

    assert result.decision_type == "hold"
    assert result.operation_advice == "持有"
    assert result.dashboard["core_conclusion"]["position_advice"]["no_position"] == "空仓者以观望为主，等待风险出清或新催化确认。"
    holder_record = next(record for record in report.applied_rules if record.rule_name == "holder-structure")
    assert holder_record.severity == "hard_guardrail"
    assert holder_record.reason_code == "holder_structure_distributed_risk_buy_downgraded"
    assert "decision_type" in holder_record.modified_fields
    assert "operation_advice" in holder_record.modified_fields
    assert holder_record.field_transitions["decision_type"] == {"before": "buy", "after": "hold"}
    assert holder_record.field_transitions["operation_advice"] == {"before": "买入", "after": "持有"}


def test_normalization_guardrail_softens_buy_when_holder_structure_is_concentrated_but_intel_is_empty() -> None:
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=80,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
        dashboard={
            "core_conclusion": {
                "one_sentence": "筹码偏集中，买入值得跟进。",
                "position_advice": {
                    "no_position": "空仓者可以积极买入",
                    "has_position": "持仓者继续加仓",
                },
            },
            "intelligence": {
                "risk_alerts": [],
                "positive_catalysts": [],
                "latest_news": "",
                "earnings_outlook": "",
            },
            "data_perspective": {
                "institution_structure": {
                    "holder_structure_bias": "集中",
                    "holder_structure_note": "前十大净增持 + 户数下降，筹码集中。",
                }
            },
        },
    )

    report = normalize_analysis_result(
        result,
        AnalysisNormalizationContext(portfolio_context=PortfolioContext(has_position=False)),
    )

    assert result.decision_type == "hold"
    assert result.operation_advice == "持有"
    assert result.dashboard["core_conclusion"]["one_sentence"] == "筹码虽偏集中，但消息催化不足，先等待进一步确认。"
    assert result.dashboard["core_conclusion"]["position_advice"]["no_position"] == "空仓者不宜仅凭筹码集中就激进买入，等待消息或业绩催化。"
    holder_record = next(record for record in report.applied_rules if record.rule_name == "holder-structure")
    assert holder_record.severity == "warning"
    assert holder_record.reason_code == "holder_structure_concentrated_no_intel_buy_softened"
    assert "decision_type" in holder_record.modified_fields
    assert "operation_advice" in holder_record.modified_fields


def test_normalization_holder_structure_guardrail_respects_english_report_language() -> None:
    result = AnalysisResult(
        code="600519",
        name="Kweichow Moutai",
        report_language="en",
        sentiment_score=80,
        trend_prediction="Bullish",
        operation_advice="Buy",
        decision_type="buy",
        dashboard={
            "core_conclusion": {
                "one_sentence": "Holder concentration looks constructive, but catalysts are still missing.",
                "position_advice": {
                    "no_position": "No position holders can buy now.",
                    "has_position": "Existing holders can keep tracking.",
                },
            },
            "intelligence": {
                "risk_alerts": [],
                "positive_catalysts": [],
                "latest_news": "",
                "earnings_outlook": "",
            },
            "data_perspective": {
                "institution_structure": {
                    "holder_structure_bias": "集中",
                    "holder_structure_note": "Top holders are accumulating.",
                }
            },
        },
    )

    normalize_analysis_result(
        result,
        AnalysisNormalizationContext(portfolio_context=PortfolioContext(has_position=False)),
    )

    assert result.decision_type == "hold"
    assert result.operation_advice == "Hold"
    assert result.dashboard["core_conclusion"]["one_sentence"] == "Holder concentration looks constructive, but catalysts are still missing. Wait for confirmation."
    assert result.dashboard["core_conclusion"]["position_advice"]["no_position"] == "Do not chase solely on holder concentration; wait for news or earnings catalysts."
    assert result.dashboard["core_conclusion"]["position_advice"]["has_position"] == "Existing holders can keep tracking, but should avoid overconfidence without catalysts."


def test_normalize_analysis_result_returns_report_summary() -> None:
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=60,
        trend_prediction="震荡",
        operation_advice="",
        decision_type="BUY",
    )

    report = normalize_analysis_result(
        result,
        AnalysisNormalizationContext(portfolio_context=PortfolioContext(has_position=False)),
    )
    result.normalization_report = report.to_dict()

    assert report.total_rules >= 1
    assert report.changed_rule_count >= 1
    assert report.modified_fields
    assert any(record.severity == "info" for record in report.applied_rules)
    assert any(record.reason_code == "decision_signal_normalized" for record in report.applied_rules if record.changed)
    assert result.to_dict()["normalization_report"]["changed_rule_count"] >= 1

def test_pipeline_sets_candidate_layer_score_before_normalization_gate() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from src.core.pipeline import StockAnalysisPipeline

    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline._emit_progress = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.analyzer.analyze.return_value = AnalysisResult(
        code="002138",
        name="顺络电子",
        sentiment_score=68,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
    )
    pipeline._build_candidate_layer_score = MagicMock(return_value={"score": 11, "trade_bias": "watch", "rating": "★★★☆☆ 关注"})

    inputs = SimpleNamespace(
        stock_name="顺络电子",
        realtime_quote=None,
        chip_data=None,
        fundamental_context=None,
        trend_result=None,
        portfolio_context=None,
        current_price=35.23,
        daily_df=None,
        daily_source="test",
    )
    context = {"code": "002138", "realtime": {"price": 35.23, "change_pct": 0.1}}

    pipeline.config = SimpleNamespace()
    pipeline.search_service = None
    pipeline.social_sentiment_service = None
    pipeline.db = MagicMock()
    pipeline.db.get_analysis_context.return_value = context
    pipeline._build_technical_factor_summary_for_analysis = MagicMock(return_value=None)
    pipeline._enhance_context = MagicMock(return_value=context)

    result = pipeline._run_traditional_analysis(
        code="002138",
        report_type=None,
        query_id="test-query",
        inputs=inputs,
    )

    assert result.decision_type == "hold"
    assert result.operation_advice == "持有"
    assert result.candidate_layer_score["score"] == 11
    assert "l2_candidate_gate_buy_blocked" in result.normalization_report["reason_codes"]


def test_normalization_l2_gate_blocks_buy_below_near_strong_threshold() -> None:
    result = AnalysisResult(
        code="002138",
        name="顺络电子",
        sentiment_score=72,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
        dashboard={
            "core_conclusion": {
                "one_sentence": "模型认为可以买入。",
                "position_advice": {
                    "no_position": "空仓者可以买入",
                    "has_position": "持仓者可以加仓",
                },
            }
        },
    )
    result.candidate_layer_score = {
        "score": 13,
        "rating": "★★★☆☆ 关注",
        "trade_bias": "watch",
        "entry_hint": "观察为主，等待右侧结构修复。",
    }

    report = normalize_analysis_result(result, AnalysisNormalizationContext())

    assert result.decision_type == "hold"
    assert result.operation_advice == "持有"
    assert result.dashboard["core_conclusion"]["position_advice"]["no_position"] == "L2二筛未达到交易门槛，空仓者不买入，等待重新评分或右侧确认。"
    assert "l2_candidate_gate_buy_blocked" in report.reason_codes
    gate_record = next(record for record in report.applied_rules if record.rule_name == "l2-candidate-gate")
    assert gate_record.severity == "hard_guardrail"
    assert "decision_type" in gate_record.modified_fields
    assert "operation_advice" in gate_record.modified_fields


def test_normalization_l2_gate_holds_near_strong_buy_for_observation() -> None:
    result = AnalysisResult(
        code="002906",
        name="华阳集团",
        sentiment_score=72,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
        dashboard={
            "core_conclusion": {
                "one_sentence": "模型认为可以买入。",
                "position_advice": {
                    "no_position": "空仓者可以买入",
                    "has_position": "持仓者可以加仓",
                },
            }
        },
    )
    result.candidate_layer_score = {
        "score": 16,
        "rating": "★★★★☆ 推荐",
        "trade_bias": "watch",
        "entry_hint": "趋势结构可观察，但仍需放量突破或回踩确认。",
    }

    report = normalize_analysis_result(result, AnalysisNormalizationContext())

    assert result.decision_type == "hold"
    assert result.operation_advice == "持有"
    assert result.dashboard["core_conclusion"]["one_sentence"] == "L2二筛为近强观察，尚未进入L3交易执行；等待右侧确认。"
    assert result.dashboard["core_conclusion"]["position_advice"]["no_position"] == "近强观察，不直接买入；等待放量突破后回踩不破。"
    assert "l2_candidate_gate_near_strong_observation" in report.reason_codes
    gate_record = next(record for record in report.applied_rules if record.rule_name == "l2-candidate-gate")
    assert gate_record.severity == "hard_guardrail"


def test_normalization_l2_gate_allows_strong_right_side_candidate_to_l3() -> None:
    result = AnalysisResult(
        code="605305",
        name="中际联合",
        sentiment_score=74,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
    )
    result.candidate_layer_score = {
        "score": 19,
        "rating": "★★★★★ 强烈推荐",
        "trade_bias": "right_side_candidate",
    }

    report = normalize_analysis_result(result, AnalysisNormalizationContext())

    assert result.decision_type == "buy"
    assert result.operation_advice == "买入"
    gate_record = next(record for record in report.applied_rules if record.rule_name == "l2-candidate-gate")
    assert gate_record.changed is False
    assert gate_record.reason_code == "l2_candidate_gate_passed"
