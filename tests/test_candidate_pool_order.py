import unittest
from unittest.mock import MagicMock, patch

from src.core.pipeline import StockAnalysisPipeline


class DummyConfig:
    mx_preselect_priority = True
    mx_preselect_limit = 50
    mx_preselect_profile = 'fundamental'
    mx_preselect_query = ''


class DummyMxClient:
    enabled = True


class DummyEnrichmentService:
    def enrich_candidates(self, rows):
        # 返回顺序与输入一致，但给出可控分数
        scored = []
        score_map = {
            '000001': 10,
            '000002': 20,
            '000003': 30,
        }
        for row in rows:
            scored.append({
                'code': row['code'],
                'mx_event_score': score_map.get(row['code'], 0),
            })
        return scored


class TestCandidatePoolOrder(unittest.TestCase):
    def test_xuangu_then_portfolio_merge(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.config = DummyConfig()
        pipeline.mx_client = DummyMxClient()
        pipeline.candidate_enrichment_service = DummyEnrichmentService()
        pipeline._extract_portfolio_stock_codes = MagicMock(return_value=['000003', '000004'])

        result = StockAnalysisPipeline._build_candidate_pool(
            pipeline,
            stock_codes=['000001', '000002'],
            dry_run=False,
        )

        self.assertEqual(result['original_stock_codes'], ['000001', '000002'])
        self.assertEqual(result['mx_xuangu_pool'], ['000002', '000001'])
        self.assertEqual(result['portfolio_pool'], ['000003', '000004'])
        self.assertEqual(result['final_candidate_pool'], ['000002', '000001', '000003', '000004'])


if __name__ == '__main__':
    unittest.main()
