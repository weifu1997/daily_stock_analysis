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


def test_analyzer_format_prompt_includes_explicit_portfolio_context() -> None:
    config = _DummyConfig()
    with patch.object(GeminiAnalyzer, "_init_litellm", lambda self: setattr(self, "_litellm_available", False)):
        analyzer = GeminiAnalyzer(
            config=config,
            skill_instructions="",
            default_skill_policy="",
            use_legacy_default_prompt=False,
        )

    prompt = analyzer._format_prompt(
        {
            "code": "600519",
            "date": "2026-01-06",
            "today": {"close": 1500.0, "open": 1490.0, "high": 1510.0, "low": 1488.0, "pct_chg": 1.2},
            "portfolio_context": {
                "has_position": True,
                "quantity": 100.0,
                "cost_basis": 1450.0,
                "unrealized_pnl": 5000.0,
                "valuation_currency": "CNY",
                "source": "portfolio_snapshot",
            },
        },
        name="贵州茅台",
        news_context=None,
        report_language="zh",
    )

    assert "持仓上下文（显式输入）" in prompt
    assert "当前持仓状态 | 持仓中" in prompt
    assert "持仓数量 | 100.0" in prompt
    assert "不要自行猜测" in prompt


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
    assert any("dashboard.core_conclusion.position_advice.no_position" == field for field in portfolio_record.modified_fields)
    assert set(portfolio_record.field_transitions) == {"decision_type", "operation_advice"}


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
    assert result.dashboard["core_conclusion"]["one_sentence"] == "筹码分散且风险偏多，暂不宜激进买入，先观望确认。"
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
        sentiment_score=78,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
        dashboard={
            "core_conclusion": {
                "one_sentence": "筹码集中，建议积极布局。",
                "position_advice": {
                    "no_position": "空仓者可直接买入跟进",
                    "has_position": "持仓者可继续拿住等待抬升",
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
                    "holder_structure_note": "前十大净增持 + 户数下降。",
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
        code="AAPL",
        name="Apple",
        sentiment_score=78,
        trend_prediction="Bullish",
        operation_advice="Buy",
        decision_type="buy",
        report_language="en",
        dashboard={
            "core_conclusion": {
                "one_sentence": "Holder structure looks concentrated, buy aggressively.",
                "position_advice": {
                    "no_position": "Open a position immediately",
                    "has_position": "Keep pressing the position",
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
                    "holder_structure_note": "Top holders increased while holder count fell.",
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
