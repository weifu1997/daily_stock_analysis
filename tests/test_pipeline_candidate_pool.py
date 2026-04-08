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

from src.core.pipeline import StockAnalysisPipeline


class TestPipelineCandidatePool(unittest.TestCase):
    @staticmethod
    def _build_pipeline(*, mx_enabled=True, mx_priority=True, mx_limit=50, profile="fundamental"):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.mx_client = SimpleNamespace(enabled=mx_enabled)
        pipeline.config = SimpleNamespace(
            mx_preselect_priority=mx_priority,
            mx_preselect_limit=mx_limit,
            mx_preselect_profile=profile,
            mx_preselect_query="A股 正常交易 低估值 高ROE 业绩稳定 现金流较好 财务健康 排除ST 排除停牌",
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


if __name__ == "__main__":
    unittest.main()
