# -*- coding: utf-8 -*-
"""Tests for analyzer news prompt hard constraints (Issue #697)."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.analyzer import GeminiAnalyzer


class AnalyzerNewsPromptTestCase(unittest.TestCase):
    def test_analysis_prompt_resolves_shared_skill_prompt_state_by_default(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        fake_state = SimpleNamespace(
            skill_instructions="### 技能 1: 波段低吸\n- 关注支撑确认",
            default_skill_policy="",
        )
        with patch("src.agent.factory.resolve_skill_prompt_state", return_value=fake_state):
            prompt = analyzer._get_analysis_system_prompt("zh", stock_code="600519")

        self.assertIn("### 技能 1: 波段低吸", prompt)
        self.assertNotIn("专注于趋势交易", prompt)

    def test_analysis_prompt_uses_injected_skill_sections_instead_of_hardcoded_trend_baseline(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(
                skill_instructions="### 技能 1: 缠论\n- 关注中枢与背驰",
                default_skill_policy="",
            )

        prompt = analyzer._get_analysis_system_prompt("zh", stock_code="600519")

        self.assertIn("### 技能 1: 缠论", prompt)
        self.assertNotIn("专注于趋势交易", prompt)
        self.assertNotIn("多头排列：MA5 > MA10 > MA20", prompt)

    def test_analysis_prompt_keeps_injected_default_policy_for_implicit_default_run(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(
                skill_instructions="### 技能 1: 默认多头趋势",
                default_skill_policy="## 默认技能基线（必须严格遵守）\n- **多头排列必须条件**：MA5 > MA10 > MA20",
                use_legacy_default_prompt=True,
            )

        prompt = analyzer._get_analysis_system_prompt("zh", stock_code="600519")

        self.assertIn("专注于趋势交易", prompt)
        self.assertIn("多头排列必须条件", prompt)
        self.assertIn("多头排列：MA5 > MA10 > MA20", prompt)

    def test_prompt_contains_time_constraints(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-03-16",
            "today": {},
            "fundamental_context": {
                "earnings": {
                    "data": {
                        "financial_report": {"report_date": "2025-12-31", "revenue": 1000},
                        "dividend": {"ttm_cash_dividend_per_share": 1.2, "ttm_dividend_yield_pct": 2.4},
                    }
                }
            },
        }
        fake_cfg = SimpleNamespace(
            news_max_age_days=30,
            news_strategy_profile="medium",  # 7 days
        )
        with patch("src.analyzer.get_config", return_value=fake_cfg):
            prompt = analyzer._format_prompt(context, "贵州茅台", news_context="news")

        self.assertIn("近7日的新闻搜索结果", prompt)
        self.assertIn("每一条都必须带具体日期（YYYY-MM-DD）", prompt)
        self.assertIn("超出近7日窗口的新闻一律忽略", prompt)
        self.assertIn("时间未知、无法确定发布日期的新闻一律忽略", prompt)
        self.assertIn("财报与分红（价值投资口径）", prompt)
        self.assertIn("禁止编造", prompt)

    def test_prompt_includes_structured_disclosure_dates_when_available(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        context = {
            "code": "002906",
            "stock_name": "华阳集团",
            "date": "2026-03-16",
            "today": {},
            "fundamental_context": {
                "earnings": {
                    "data": {
                        "financial_report": {"report_date": "2025-12-31", "revenue": 1000},
                        "disclosure_date": {
                            "report_date": "2025-12-31",
                            "ann_date": "2026-03-18",
                            "actual_date": "2026-03-20",
                            "pre_date": "2026-03-19",
                        },
                    }
                }
            },
        }
        fake_cfg = SimpleNamespace(
            news_max_age_days=30,
            news_strategy_profile="medium",
        )
        with patch("src.analyzer.get_config", return_value=fake_cfg):
            prompt = analyzer._format_prompt(context, "华阳集团", news_context="news")

        self.assertIn("财报披露日期（结构化）", prompt)
        self.assertIn("预约披露日期 | 2026-03-19", prompt)
        self.assertIn("公告披露日期 | 2026-03-18", prompt)
        self.assertIn("实际披露日期 | 2026-03-20", prompt)
        self.assertIn("优先以结构化披露日期判断业绩催化时点", prompt)

    def test_prompt_includes_institution_holder_signals_when_available(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        context = {
            "code": "002906",
            "stock_name": "华阳集团",
            "date": "2026-03-16",
            "today": {},
            "fundamental_context": {
                "earnings": {"data": {"financial_report": {"report_date": "2025-12-31", "revenue": 1000}}},
                "institution": {
                    "data": {
                        "top10_holder_change": -4484943.0,
                        "holder_num_change": -81,
                        "holder_num": 41060,
                        "holder_num_end_date": "2026-04-10",
                    }
                },
            },
        }
        fake_cfg = SimpleNamespace(
            news_max_age_days=30,
            news_strategy_profile="medium",
        )
        with patch("src.analyzer.get_config", return_value=fake_cfg):
            prompt = analyzer._format_prompt(context, "华阳集团", news_context="news")

        self.assertIn("机构/股东结构（结构化）", prompt)
        self.assertIn("前十大股东净变动 | -4484943.0", prompt)
        self.assertIn("股东户数变动 | -81", prompt)
        self.assertIn("请结合前十大股东净变动与股东户数变动判断筹码分散/集中趋势", prompt)

    def test_prompt_prefers_context_news_window_days(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-03-16",
            "today": {},
            "news_window_days": 1,
        }
        fake_cfg = SimpleNamespace(
            news_max_age_days=30,
            news_strategy_profile="long",  # 30 days if fallback is used
        )
        with patch("src.analyzer.get_config", return_value=fake_cfg):
            prompt = analyzer._format_prompt(context, "贵州茅台", news_context="news")

        self.assertIn("近1日的新闻搜索结果", prompt)
        self.assertIn("超出近1日窗口的新闻一律忽略", prompt)

    def test_format_prompt_omits_legacy_trend_checks_for_nondefault_skill_mode(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(
                skill_instructions="### 技能 1: 缠论\n- 关注中枢与背驰",
                default_skill_policy="",
                use_legacy_default_prompt=False,
            )

        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-03-16",
            "today": {"close": 100, "ma5": 99, "ma10": 98, "ma20": 97},
            "trend_analysis": {
                "trend_status": "震荡偏强",
                "ma_alignment": "粘合后发散",
                "trend_strength": 61,
                "bias_ma5": 1.2,
                "bias_ma10": 2.4,
                "volume_status": "平量",
                "volume_trend": "量能温和",
                "buy_signal": "观察",
                "signal_score": 58,
                "signal_reasons": ["结构待确认"],
                "risk_factors": ["无背驰确认"],
            },
        }
        prompt = analyzer._format_prompt(context, "贵州茅台", news_context=None)

        self.assertIn("当前结构是否满足激活技能的关键触发条件", prompt)
        self.assertNotIn("是否满足 MA5>MA10>MA20 多头排列", prompt)
        self.assertNotIn("超过5%必须标注\"严禁追高\"", prompt)
        self.assertNotIn("MA5>MA10>MA20为多头", prompt)


if __name__ == "__main__":
    unittest.main()
