# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main
from src.runtime.execution_report import ExecutionReport


class _Config(SimpleNamespace):
    def refresh_stock_list(self):
        return None


def _make_args(**overrides):
    defaults = {
        "workers": 1,
        "dry_run": False,
        "no_context_snapshot": False,
        "single_notify": False,
        "no_market_review": False,
        "force_run": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_config(**overrides):
    defaults = {
        "stock_list": ["600519"],
        "merge_email_notification": False,
        "market_review_enabled": False,
        "single_stock_notify": False,
        "analysis_delay": 0,
        "backtest_enabled": False,
        "report_type": "simple",
    }
    defaults.update(overrides)
    return _Config(**defaults)


def test_run_full_analysis_returns_success_execution_report():
    args = _make_args()
    config = _make_config()
    pipeline = MagicMock()
    pipeline.run.return_value = [SimpleNamespace(
        code="600519",
        name="贵州茅台",
        sentiment_score=88,
        operation_advice="买入",
        trend_prediction="看多",
        get_emoji=lambda: "📈",
        normalization_report={
            "changed_rule_count": 1,
            "max_severity": "hard_guardrail",
            "reason_codes": ["portfolio_non_holder_action_adjusted"],
            "applied_rules": [
                {
                    "rule_name": "portfolio-context",
                    "changed": True,
                    "severity": "hard_guardrail",
                    "reason_code": "portfolio_non_holder_action_adjusted",
                    "modified_fields": ["operation_advice"],
                    "field_transitions": {
                        "operation_advice": {"before": "加仓", "after": "观望"},
                    },
                }
            ],
        },
    )]
    pipeline.notifier = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.search_service = MagicMock()

    with patch("main._compute_trading_day_filter", return_value=(["600519"], "cn", False)), \
         patch("src.core.pipeline.StockAnalysisPipeline", return_value=pipeline), \
         patch("src.core.market_review.run_market_review", return_value=""):
        report = main.run_full_analysis(config, args)

    assert isinstance(report, ExecutionReport)
    assert report.success is True
    assert report.degraded is False
    assert report.fatal_error is None
    assert report.artifacts.get("query_id")
    normalization_summary = report.artifacts.get("normalization_summary")
    assert normalization_summary["changed_result_count"] == 1
    assert normalization_summary["hard_guardrail_count"] == 1
    assert normalization_summary["warning_count"] == 0
    assert normalization_summary["info_count"] == 0
    assert normalization_summary["reason_code_counts"]["portfolio_non_holder_action_adjusted"] == 1
    assert normalization_summary["top_reason_codes"][0]["reason_code"] == "portfolio_non_holder_action_adjusted"
    assert normalization_summary["transition_counts"]["portfolio_non_holder_action_adjusted::加仓->观望"] == 1
    assert normalization_summary["top_transitions"][0]["reason_code"] == "portfolio_non_holder_action_adjusted"
    assert normalization_summary["top_transitions"][0]["before_operation_advice"] == "加仓"
    assert normalization_summary["top_transitions"][0]["after_operation_advice"] == "观望"
    assert normalization_summary["stocks_with_hard_guardrail"] == ["600519"]


def test_run_full_analysis_returns_failure_report_on_runtime_exception():
    args = _make_args()
    config = _make_config()

    with patch("main._compute_trading_day_filter", side_effect=RuntimeError("boom during filter")):
        report = main.run_full_analysis(config, args)

    assert isinstance(report, ExecutionReport)
    assert report.success is False
    assert report.failed is True
    assert report.fatal_error == "boom during filter"


def test_run_full_analysis_marks_degraded_when_optional_step_fails():
    args = _make_args()
    config = _make_config()
    pipeline = MagicMock()
    pipeline.run.return_value = [SimpleNamespace(
        sentiment_score=90,
        name="贵州茅台",
        code="600519",
        operation_advice="持有",
        trend_prediction="看多",
        get_emoji=lambda: "📈",
    )]
    pipeline.notifier = MagicMock()
    pipeline.notifier.is_available.return_value = False
    pipeline.analyzer = MagicMock()
    pipeline.search_service = MagicMock()

    with patch("main._compute_trading_day_filter", return_value=(["600519"], "cn", False)), \
         patch("src.core.pipeline.StockAnalysisPipeline", return_value=pipeline), \
         patch("src.core.market_review.run_market_review", return_value=""), \
         patch("main.save_plan", side_effect=RuntimeError("disk full")):
        report = main.run_full_analysis(config, args)

    assert isinstance(report, ExecutionReport)
    assert report.success is True
    assert report.degraded is True
    assert any(item.name == "moni_plan" for item in report.degraded_components)
