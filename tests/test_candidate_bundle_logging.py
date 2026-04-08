import unittest
from unittest.mock import patch

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
        return [
            {'code': '000001', 'mx_event_score': 10},
            {'code': '000002', 'mx_event_score': 20},
        ]


class TestCandidateBundleLogging(unittest.TestCase):
    def test_run_logs_new_mx_xuangu_field_name(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.config = DummyConfig()
        pipeline.mx_client = DummyMxClient()
        pipeline.candidate_enrichment_service = DummyEnrichmentService()
        pipeline._extract_portfolio_stock_codes = lambda: ['600000']
        pipeline.max_workers = 1
        pipeline.fetcher_manager = type('FM', (), {
            'prefetch_realtime_quotes': lambda self, codes: 0,
            'prefetch_stock_names': lambda self, codes, use_bulk=False: None,
        })()
        pipeline.process_single_stock = lambda *args, **kwargs: None
        pipeline.db = type('DB', (), {'has_today_data': lambda *args, **kwargs: False})()
        pipeline.notifier = type('N', (), {'is_available': lambda self: False})()
        pipeline._send_single_stock_notification = lambda *args, **kwargs: None
        pipeline._save_local_report = lambda *args, **kwargs: None
        pipeline._send_notifications = lambda *args, **kwargs: None
        pipeline._resolve_resume_target_date = lambda *args, **kwargs: '2026-04-08'

        with patch.object(StockAnalysisPipeline, '_build_candidate_pool', return_value={
            'original_stock_codes': ['000001', '000002'],
            'mx_xuangu_pool': ['000002', '000001'],
            'portfolio_pool': ['600000'],
            'final_candidate_pool': ['000002', '000001', '600000'],
            'fallback_used': False,
        }):
            with patch('src.core.pipeline.logger') as mock_logger:
                try:
                    StockAnalysisPipeline.run(pipeline, stock_codes=['000001', '000002'], dry_run=True, send_notification=False)
                except Exception:
                    # 只验证日志字段名，不关心后续执行细节
                    pass

        logged = ' '.join(str(call.args[0]) for call in mock_logger.info.call_args_list if call.args)
        self.assertIn('mx_xuangu', logged)
        self.assertNotIn('候选池结构: original=%d, mx=%d', logged)


if __name__ == '__main__':
    unittest.main()
