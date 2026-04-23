# -*- coding: utf-8 -*-
"""
Report quality context tests.
"""

import sys
import unittest
from unittest.mock import MagicMock

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.analyzer import AnalysisResult
from src.core.pipeline import StockAnalysisPipeline


class TestReportQualityContext(unittest.TestCase):
    def test_build_report_quality_context_map_classifies_real_chip_and_news(self) -> None:
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            dashboard={
                "data_perspective": {
                    "chip_structure": {
                        "source_category": "real",
                        "data_reliability": "real_chip",
                        "source": "tushare_cyq_perf/tushare_cyq_chips",
                    }
                },
                "intelligence": {"latest_news": "业绩稳定"},
            },
            market_snapshot={"close": 180.0},
            search_performed=True,
        )

        quality_map = StockAnalysisPipeline._build_report_quality_context_map(pipeline, [result])
        quality = quality_map["600519"]

        self.assertEqual(quality["report_reliability"], "high")
        self.assertTrue(quality["has_real_chip"])
        self.assertTrue(quality["has_valid_news"])
        self.assertTrue(quality["has_market_snapshot"])
        self.assertFalse(quality["fallback_used"])

    def test_build_report_quality_context_map_marks_fallback_when_snapshot_missing(self) -> None:
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        result = AnalysisResult(
            code="000001",
            name="测试股票",
            sentiment_score=50,
            trend_prediction="震荡",
            operation_advice="观望",
            dashboard={
                "data_perspective": {
                    "chip_structure": {
                        "source_category": "estimated",
                        "data_reliability": "fallback_estimated",
                        "is_estimated": True,
                        "source": "estimated_ohlcv",
                    }
                },
                "intelligence": {},
            },
            search_performed=False,
        )

        quality_map = StockAnalysisPipeline._build_report_quality_context_map(pipeline, [result])
        quality = quality_map["000001"]

        self.assertEqual(quality["report_reliability"], "low")
        self.assertFalse(quality["has_real_chip"])
        self.assertFalse(quality["has_valid_news"])
        self.assertFalse(quality["has_market_snapshot"])
        self.assertTrue(quality["fallback_used"])


if __name__ == "__main__":
    unittest.main()
