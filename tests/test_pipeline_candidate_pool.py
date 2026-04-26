# -*- coding: utf-8 -*-
"""Regression tests for candidate pool preselection in StockAnalysisPipeline."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.analyzer import AnalysisResult
from src.core.pipeline import StockAnalysisPipeline
from src.runtime.mx_preselect import MX_PRESELECT_EXCLUDE_TOKENS, MX_PRESELECT_REQUIRED_TOKENS


class TestPipelineCandidatePool(unittest.TestCase):
    @staticmethod
    def _build_pipeline(*, mx_enabled=True, mx_priority=True, mx_limit=50, profile="fundamental"):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.mx_client = SimpleNamespace(enabled=mx_enabled)
        pipeline.config = SimpleNamespace(
            mx_preselect_priority=mx_priority,
            mx_preselect_limit=mx_limit,
            mx_preselect_profile=profile,
            mx_preselect_query="A股 正常交易 非ST 非停牌 低估值 高ROE 业绩稳定 经营现金流良好 财务健康 排除科创板 排除创业板 排除北交所",
        )
        pipeline.candidate_enrichment_service = MagicMock()
        pipeline.candidate_enrichment_service.enrich_candidates.side_effect = lambda rows: [
            {**row, "mx_event_score": score}
            for row, score in zip(rows, range(len(rows), 0, -1))
        ]
        pipeline._extract_portfolio_stock_codes = MagicMock(return_value=["000776", "600120"])
        return pipeline

    def test_mx_preselect_priority_orders_and_forces_portfolio(self):
        pipeline = self._build_pipeline(mx_enabled=True, mx_priority=True, mx_limit=10)

        result = pipeline._build_candidate_pool(["600519", "000001", "600519"], dry_run=True)

        self.assertTrue(result["mx_xuangu_enabled"])
        self.assertFalse(result["fallback_used"])
        self.assertEqual(result["original_stock_codes"], ["600519", "000001"])
        self.assertEqual(result["portfolio_pool"], ["000776", "600120"])
        self.assertEqual(result["final_candidate_pool"], ["600519", "000001", "000776", "600120"])
        self.assertEqual(result["candidate_source_map"]["600519"]["candidate_source"], "mx_preselect")
        self.assertEqual(result["candidate_source_map"]["600519"]["source_rank"], 1)
        self.assertEqual(result["candidate_source_map"]["600519"]["source_query"], pipeline.config.mx_preselect_query)
        self.assertEqual(result["candidate_source_map"]["600519"]["preselect_rule_set"]["required_tokens"], list(MX_PRESELECT_REQUIRED_TOKENS))
        self.assertTrue(set(MX_PRESELECT_EXCLUDE_TOKENS).issubset(set(result["candidate_source_map"]["600519"]["preselect_rule_set"]["exclude_tokens"])))
        self.assertFalse(result["candidate_source_map"]["600519"]["forced_by_portfolio"])
        self.assertEqual(result["candidate_source_map"]["000776"]["candidate_source"], "portfolio")
        self.assertTrue(result["candidate_source_map"]["000776"]["forced_by_portfolio"])
        self.assertEqual(result["candidate_source_map"]["000776"]["pool_reason"], "portfolio_forced_include")
        self.assertEqual(
            result["candidate_source_rows"],
            [result["candidate_source_map"][code] for code in result["final_candidate_pool"]],
        )
        self.assertEqual(
            pipeline.candidate_enrichment_service.enrich_candidates.call_count,
            1,
        )

    def test_mx_preselect_limit_still_keeps_portfolio(self):
        pipeline = self._build_pipeline(mx_enabled=True, mx_priority=True, mx_limit=2)

        result = pipeline._build_candidate_pool(["600519", "000001", "000002", "000003"], dry_run=True)

        self.assertEqual(result["final_candidate_pool"], ["600519", "000001", "000776", "600120"])
        self.assertIn("limit=2", result["mx_xuangu_reason"])

    def test_mx_disabled_falls_back_to_original_plus_portfolio(self):
        pipeline = self._build_pipeline(mx_enabled=False, mx_priority=True, mx_limit=10)

        result = pipeline._build_candidate_pool(["600519", "000001"], dry_run=True)

        self.assertFalse(result["mx_xuangu_enabled"])
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["final_candidate_pool"], ["600519", "000001", "000776", "600120"])
        self.assertEqual(result["candidate_source_map"]["600519"]["candidate_source"], "fallback_original")
        self.assertTrue(result["candidate_source_map"]["600519"]["fallback_used"])
        self.assertEqual(result["candidate_source_map"]["600519"]["pool_reason"], "mx_unavailable_or_failed")
        self.assertEqual(result["candidate_source_map"]["600519"]["preselect_rule_set"], {})
        self.candidate_enrichment_service = pipeline.candidate_enrichment_service
        self.candidate_enrichment_service.enrich_candidates.assert_not_called()

    def test_mx_exception_falls_back_to_original_plus_portfolio(self):
        pipeline = self._build_pipeline(mx_enabled=True, mx_priority=True, mx_limit=10)
        pipeline.candidate_enrichment_service.enrich_candidates.side_effect = RuntimeError("boom")

        result = pipeline._build_candidate_pool(["600519", "000001"], dry_run=True)

        self.assertTrue(result["fallback_used"])
        self.assertIn("mx_xuangu failed", result["mx_xuangu_reason"])
        self.assertEqual(result["final_candidate_pool"], ["600519", "000001", "000776", "600120"])

    def test_mx_empty_result_still_keeps_original_and_portfolio(self):
        pipeline = self._build_pipeline(mx_enabled=True, mx_priority=True, mx_limit=10)
        pipeline.candidate_enrichment_service.enrich_candidates.return_value = []

        result = pipeline._build_candidate_pool(["600519", "000001"], dry_run=True)

        self.assertFalse(result["fallback_used"])
        self.assertEqual(result["mx_candidate_pool"], ["600519", "000001", "000776", "600120"])
        self.assertEqual(result["final_candidate_pool"], ["600519", "000001", "000776", "600120"])

    def test_report_extra_context_includes_l3_execution_plan_map(self):
        pipeline = self._build_pipeline(mx_enabled=True, mx_priority=True, mx_limit=10)
        pipeline._build_portfolio_context_map = MagicMock(return_value={})
        pipeline._build_report_quality_context_map = MagicMock(return_value={})
        pipeline._build_report_decision_context_map = MagicMock(return_value={})
        pipeline._get_cached_portfolio_snapshot = MagicMock(return_value={"total_cash": 50_000, "total_equity": 200_000, "accounts": []})
        result = AnalysisResult(code="605305", name="中际联合", sentiment_score=74, trend_prediction="看多", operation_advice="买入", current_price=35.2)
        result.candidate_layer_score = {"score": 19, "trade_bias": "right_side_candidate"}

        context = pipeline._build_report_extra_context([result])

        self.assertIn("execution_plan_map", context)
        self.assertTrue(context["execution_plan_map"]["605305"]["eligible_for_l3"])
        self.assertEqual(context["execution_plan_map"]["605305"]["hard_stop_loss_pct"], -8)


if __name__ == '__main__':
    unittest.main()
