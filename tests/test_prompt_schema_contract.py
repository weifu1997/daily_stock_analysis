"""Prompt/schema contract tests for Issue 5."""

from __future__ import annotations

import unittest
from pathlib import Path

from jinja2 import Environment, meta

from src.analyzer import AnalysisResult, PROMPT_VERSION
from src.services import report_renderer


TEMPLATE_NAMES = ["report_markdown.j2", "report_brief.j2", "report_wechat.j2"]
ALLOWED_ROOTS = report_renderer._ALLOWED_TEMPLATE_ROOTS
EXPECTED_FIELD_SOURCES = {
    "AnalysisResult.stock_name",
    "AnalysisResult.sentiment_score",
    "AnalysisResult.trend_prediction",
    "AnalysisResult.operation_advice",
    "AnalysisResult.decision_type",
    "AnalysisResult.confidence_level",
    "AnalysisResult.dashboard",
    "AnalysisResult.analysis_summary",
    "AnalysisResult.key_points",
    "AnalysisResult.risk_warning",
    "AnalysisResult.buy_reason",
    "AnalysisResult.trend_analysis",
    "AnalysisResult.short_term_outlook",
    "AnalysisResult.medium_term_outlook",
    "AnalysisResult.technical_analysis",
    "AnalysisResult.ma_analysis",
    "AnalysisResult.volume_analysis",
    "AnalysisResult.pattern_analysis",
    "AnalysisResult.fundamental_analysis",
    "AnalysisResult.sector_position",
    "AnalysisResult.company_highlights",
    "AnalysisResult.news_summary",
    "AnalysisResult.market_sentiment",
    "AnalysisResult.hot_topics",
    "AnalysisResult.search_performed",
    "AnalysisResult.data_sources",
    "AnalysisResult.prompt_version",
    "AnalysisResult.report_language",
    "AnalysisResult.model_used",
    "AnalysisResult.current_price",
    "AnalysisResult.change_pct",
}


def _extract_roots(text: str) -> set[str]:
    env = Environment()
    parsed = env.parse(text)
    return set(meta.find_undeclared_variables(parsed))


class TestPromptSchemaContract(unittest.TestCase):
    def test_analysis_result_round_trip_exposes_prompt_version(self) -> None:
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            prompt_version=PROMPT_VERSION,
        )
        payload = result.to_dict()
        self.assertEqual(payload["prompt_version"], PROMPT_VERSION)

    def test_templates_only_use_known_context_roots(self) -> None:
        templates_dir = Path(report_renderer._resolve_templates_dir())
        for template_name in TEMPLATE_NAMES:
            template_text = (templates_dir / template_name).read_text(encoding="utf-8")
            roots = _extract_roots(template_text)
            unknown = sorted(root for root in roots if root not in ALLOWED_ROOTS)
            self.assertEqual(unknown, [], msg=f"{template_name} has unknown roots: {unknown}")

    def test_contract_mapping_covers_expected_analysis_result_fields(self) -> None:
        mapping = report_renderer._load_contract_field_mapping()
        missing = sorted(source for source in EXPECTED_FIELD_SOURCES if source not in set(mapping.values()))
        self.assertEqual(missing, [], msg=f"missing field sources: {missing}")

    def test_contract_doc_exists_and_mentions_prompt_version(self) -> None:
        contract_path = Path("docs/contracts/prompt-to-result-mapping.md")
        self.assertTrue(contract_path.exists())
        text = contract_path.read_text(encoding="utf-8")
        self.assertIn("report_markdown.j2", text)
        self.assertIn("report_brief.j2", text)
        self.assertIn("report_wechat.j2", text)
        self.assertIn("prompt_version", text)
        self.assertIn("## AnalysisResult fields tracked", text)
        self.assertIn("AnalysisResult.prompt_version", text)
        self.assertIn("AnalysisResult.analysis_summary", text)
        self.assertIn("AnalysisResult.dashboard", text)
