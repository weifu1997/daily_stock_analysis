# -*- coding: utf-8 -*-
"""
===================================
Report Engine - Report renderer tests
===================================

Tests for Jinja2 report rendering and fallback behavior.
"""

import sys
import unittest
from unittest.mock import MagicMock

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.analyzer import AnalysisResult
from src.services.report_renderer import render


def _make_result(
    code: str = "600519",
    name: str = "贵州茅台",
    sentiment_score: int = 72,
    operation_advice: str = "持有",
    analysis_summary: str = "稳健",
    decision_type: str = "hold",
    dashboard: dict = None,
    report_language: str = "zh",
) -> AnalysisResult:
    if dashboard is None:
        dashboard = {
            "core_conclusion": {"one_sentence": "持有观望"},
            "intelligence": {"risk_alerts": []},
            "battle_plan": {"sniper_points": {"stop_loss": "110"}},
        }
    return AnalysisResult(
        code=code,
        name=name,
        trend_prediction="看多",
        sentiment_score=sentiment_score,
        operation_advice=operation_advice,
        analysis_summary=analysis_summary,
        decision_type=decision_type,
        dashboard=dashboard,
        report_language=report_language,
    )


class TestReportRenderer(unittest.TestCase):
    """Report renderer tests."""

    def test_render_markdown_summary_only(self) -> None:
        """Markdown platform renders with summary_only."""
        r = _make_result()
        out = render("markdown", [r], summary_only=True)
        self.assertIsNotNone(out)
        self.assertIn("决策仪表盘", out)
        self.assertIn("贵州茅台", out)
        self.assertIn("持有", out)

    def test_render_markdown_summary_counts_follow_operation_advice(self) -> None:
        """Top-line counts must use operation_advice so summary and body stay aligned."""
        r = _make_result(operation_advice="观望", decision_type="sell")
        out = render("markdown", [r], summary_only=True)
        self.assertIsNotNone(out)
        self.assertIn("🟡观望:1", out)
        self.assertIn("🔴卖出:0", out)
        self.assertNotIn("🔴卖出:1", out)

    def test_render_markdown_full(self) -> None:
        """Markdown platform renders full report."""
        r = _make_result()
        out = render("markdown", [r], summary_only=False)
        self.assertIsNotNone(out)
        self.assertIn("核心结论", out)
        self.assertIn("作战计划", out)

    def test_render_markdown_full_includes_institution_holder_section(self) -> None:
        r = _make_result(
            dashboard={
                "core_conclusion": {"one_sentence": "筹码仍需观察"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
                "data_perspective": {
                    "institution_structure": {
                        "top10_holder_change": -4484943.0,
                        "holder_num": 41060,
                        "holder_num_change": -81,
                        "holder_num_end_date": "2026-04-10",
                        "holder_structure_bias": "中性",
                        "holder_structure_note": "前十大净减持 + 户数下降，存在大户退出但散户未显著接盘，筹码并非简单分散。",
                    }
                },
            }
        )
        out = render("markdown", [r], summary_only=False)
        self.assertIsNotNone(out)
        self.assertIn("机构/股东结构", out)
        self.assertIn("前十大股东净变动", out)
        self.assertIn("-4484943.0", out)
        self.assertIn("股东户数变动", out)
        self.assertIn("-81", out)
        self.assertIn("持有人结构倾向", out)
        self.assertIn("中性", out)
        self.assertIn("大户退出但散户未显著接盘", out)

    def test_render_markdown_full_appends_result_level_guardrail_note(self) -> None:
        r = _make_result(
            dashboard={
                "core_conclusion": {"one_sentence": "筹码分散且风险偏多，暂不宜激进买入，先观望确认。"},
                "intelligence": {"risk_alerts": ["大股东减持", "订单不及预期"]},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            }
        )
        r.normalization_report = {
            "applied_rules": [
                {
                    "rule_name": "holder-structure",
                    "changed": True,
                    "severity": "hard_guardrail",
                    "reason_code": "holder_structure_distributed_risk_buy_downgraded",
                    "modified_fields": ["decision_type", "operation_advice"],
                }
            ]
        }
        out = render("markdown", [r], summary_only=False)
        self.assertIsNotNone(out)
        self.assertIn("🛡️ 结论约束", out)
        self.assertIn("筹码分散且风险偏多，买入建议已降级", out)
        self.assertNotIn("holder_structure_distributed_risk_buy_downgraded", out)

    def test_render_markdown_full_appends_guardrail_transition_trace(self) -> None:
        r = _make_result(
            operation_advice="持有",
            decision_type="hold",
            dashboard={
                "core_conclusion": {"one_sentence": "等待确认。"},
                "intelligence": {"risk_alerts": ["大股东减持", "订单不及预期"]},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            },
        )
        r.normalization_report = {
            "applied_rules": [
                {
                    "rule_name": "holder-structure",
                    "changed": True,
                    "severity": "hard_guardrail",
                    "reason_code": "holder_structure_distributed_risk_buy_downgraded",
                    "modified_fields": ["decision_type", "operation_advice"],
                    "field_transitions": {
                        "decision_type": {"before": "buy", "after": "hold"},
                        "operation_advice": {"before": "买入", "after": "持有"},
                    },
                }
            ]
        }
        out = render("markdown", [r], summary_only=False)
        self.assertIsNotNone(out)
        self.assertIn("原始：买入 → 约束后：持有", out)
        self.assertIn("原因：筹码分散且风险偏多，买入建议已降级", out)

    def test_render_markdown_omits_holder_structure_rows_when_interpretation_missing(self) -> None:
        r = _make_result(
            dashboard={
                "core_conclusion": {"one_sentence": "筹码仍需观察"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
                "data_perspective": {
                    "institution_structure": {
                        "holder_num": 41060,
                        "holder_num_end_date": "2026-04-10",
                    }
                },
            }
        )
        out = render("markdown", [r], summary_only=False)
        self.assertIsNotNone(out)
        self.assertNotIn("| 持有人结构倾向 | N/A |", out)
        self.assertNotIn("| 结构解读 | N/A |", out)

    def test_render_markdown_omits_holder_structure_rows_for_placeholder_strings(self) -> None:
        r = _make_result(
            dashboard={
                "core_conclusion": {"one_sentence": "筹码仍需观察"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
                "data_perspective": {
                    "institution_structure": {
                        "holder_num": 41060,
                        "holder_num_end_date": "2026-04-10",
                        "holder_structure_bias": "N/A",
                        "holder_structure_note": "data unavailable",
                    }
                },
            }
        )
        out = render("markdown", [r], summary_only=False)
        self.assertIsNotNone(out)
        self.assertNotIn("| 持有人结构倾向 | N/A |", out)
        self.assertNotIn("data unavailable", out)

    def test_render_wechat(self) -> None:
        """Wechat platform renders."""
        r = _make_result()
        out = render("wechat", [r])
        self.assertIsNotNone(out)
        self.assertIn("贵州茅台", out)

    def test_render_brief(self) -> None:
        """Brief platform renders 3-5 sentence summary."""
        r = _make_result()
        out = render("brief", [r])
        self.assertIsNotNone(out)
        self.assertIn("决策简报", out)
        self.assertIn("贵州茅台", out)

    def test_render_markdown_in_english(self) -> None:
        """Markdown renderer switches headings and summary labels for English reports."""
        r = _make_result(
            name="Kweichow Moutai",
            operation_advice="Buy",
            analysis_summary="Momentum remains constructive.",
            report_language="en",
        )
        out = render("markdown", [r], summary_only=True)
        self.assertIsNotNone(out)
        self.assertIn("Decision Dashboard", out)
        self.assertIn("Summary", out)
        self.assertIn("Buy", out)

    def test_render_markdown_market_snapshot_uses_template_context(self) -> None:
        """Market snapshot macro should render localized labels with template context."""
        r = _make_result(
            code="AAPL",
            name="Apple",
            operation_advice="Buy",
            report_language="en",
        )
        r.market_snapshot = {
            "close": "180.10",
            "prev_close": "178.25",
            "open": "179.00",
            "high": "181.20",
            "low": "177.80",
            "pct_chg": "+1.04%",
            "change_amount": "1.85",
            "amplitude": "1.91%",
            "volume": "1200000",
            "amount": "215000000",
            "price": "180.35",
            "volume_ratio": "1.2",
            "turnover_rate": "0.8%",
            "source": "polygon",
        }

        out = render("markdown", [r], summary_only=False)

        self.assertIsNotNone(out)
        self.assertIn("Market Snapshot", out)
        self.assertIn("Volume Ratio", out)

    def test_render_unknown_platform_returns_none(self) -> None:
        """Unknown platform returns None (caller fallback)."""
        r = _make_result()
        out = render("unknown_platform", [r])
        self.assertIsNone(out)

    def test_render_empty_results_returns_content(self) -> None:
        """Empty results still produces header."""
        out = render("markdown", [], summary_only=True)
        self.assertIsNotNone(out)
        self.assertIn("0", out)

    def test_render_accepts_mx_enrichment_context(self) -> None:
        r = _make_result()
        out = render(
            "markdown",
            [r],
            extra_context={"mx_enrichment": {"mx_enabled": True, "mx_event_score": 12.0}},
        )
        self.assertIsNotNone(out)
