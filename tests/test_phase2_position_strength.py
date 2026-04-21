# -*- coding: utf-8 -*-
"""Phase 2 minimal design tests for position strength mapping."""

import os
import sys
import tempfile
import unittest

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    import types
    sys.modules["litellm"] = types.SimpleNamespace()

from src.analyzer import AnalysisResult


class TestPhase2PositionStrength(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(self._temp_dir.name, "phase2_position_strength.db")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _make_result(self, decision_type: str = "buy", operation_advice: str = "买入") -> AnalysisResult:
        result = AnalysisResult(
            code="002906",
            name="华阳集团",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice=operation_advice,
            analysis_summary="趋势和基本面都偏正面",
        )
        result.decision_type = decision_type
        return result

    def test_buy_with_low_risk_maps_to_attack(self) -> None:
        from src.analysis.normalization import AnalysisNormalizationContext, normalize_analysis_result

        result = self._make_result("buy", "买入")
        result.risk_penalty = 0.2

        normalize_analysis_result(result, AnalysisNormalizationContext())

        self.assertEqual(result.position_strength, "trial")
        self.assertEqual(result.decision_type, "buy")

    def test_buy_with_high_risk_is_downgraded_to_neutral(self) -> None:
        from src.analysis.normalization import AnalysisNormalizationContext, normalize_analysis_result

        result = self._make_result("buy", "买入")
        result.risk_penalty = 0.82
        result.decision_type = "hold"
        result.operation_advice = "持有"

        normalize_analysis_result(result, AnalysisNormalizationContext())

        self.assertEqual(result.position_strength, "neutral")
        self.assertEqual(result.decision_type, "hold")
        self.assertEqual(result.operation_advice, "持有")

    def test_sell_maps_to_defense(self) -> None:
        from src.analysis.normalization import AnalysisNormalizationContext, normalize_analysis_result

        result = self._make_result("sell", "减仓")

        normalize_analysis_result(result, AnalysisNormalizationContext())

        self.assertEqual(result.position_strength, "defense")
        self.assertEqual(result.decision_type, "sell")


if __name__ == "__main__":
    unittest.main()
