# -*- coding: utf-8 -*-
"""Tests for institution structure fallback into dashboard data_perspective."""

import sys
import unittest
from unittest.mock import MagicMock

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.analyzer import AnalysisResult, fill_institution_structure_if_needed


class TestInstitutionStructureFallback(unittest.TestCase):
    def _make_result(self, dashboard=None) -> AnalysisResult:
        return AnalysisResult(
            code="002906",
            name="华阳集团",
            trend_prediction="看多",
            sentiment_score=66,
            operation_advice="观望",
            analysis_summary="等待确认",
            decision_type="hold",
            dashboard=dashboard or {},
            report_language="zh",
        )

    def test_fill_institution_structure_from_fundamental_context(self) -> None:
        result = self._make_result(dashboard={"data_perspective": {}})
        fundamental_context = {
            "institution": {
                "data": {
                    "top10_holder_change": -4484943.0,
                    "holder_num": 41060,
                    "holder_num_change": -81,
                    "holder_num_end_date": "2026-04-10",
                }
            }
        }

        fill_institution_structure_if_needed(result, fundamental_context)

        institution = result.dashboard["data_perspective"]["institution_structure"]
        self.assertEqual(institution["top10_holder_change"], -4484943.0)
        self.assertEqual(institution["holder_num"], 41060)
        self.assertEqual(institution["holder_num_change"], -81)
        self.assertEqual(institution["holder_num_end_date"], "2026-04-10")


if __name__ == "__main__":
    unittest.main()
