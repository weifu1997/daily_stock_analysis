# -*- coding: utf-8 -*-

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.core.pipeline import StockAnalysisPipeline
from src.search.capabilities import SearchCapabilityStatus


class _DummyResult:
    success = True
    query_id = None
    current_price = None
    change_pct = None
    dashboard = {}


class _DummyTrendResult:
    trend_status = SimpleNamespace(value="bullish")
    buy_signal = SimpleNamespace(value="watch")
    signal_score = 60


def _make_config() -> SimpleNamespace:
    return SimpleNamespace(
        max_workers=2,
        save_context_snapshot=False,
        bocha_api_keys=[],
        tavily_api_keys=[],
        brave_api_keys=[],
        serpapi_keys=[],
        minimax_api_keys=[],
        searxng_base_urls=[],
        searxng_public_instances_enabled=False,
        news_max_age_days=7,
        news_strategy_profile="short",
        enable_realtime_quote=False,
        realtime_source_priority=[],
        enable_chip_distribution=False,
        social_sentiment_api_key="",
        social_sentiment_api_url="https://example.invalid/social",
        mx_base_url=None,
        mx_apikey=None,
        mx_api_key=None,
        mx_timeout_seconds=1.0,
        report_integrity_enabled=False,
        agent_mode=False,
        agent_skills=[],
        fundamental_stage_timeout_seconds=1.5,
    )


def _build_pipeline(config: SimpleNamespace) -> StockAnalysisPipeline:
    with patch("src.core.pipeline.get_db", return_value=MagicMock()), \
         patch("src.core.pipeline.DataFetcherManager", return_value=MagicMock()), \
         patch("src.core.pipeline.StockTrendAnalyzer", return_value=MagicMock()), \
         patch("src.core.pipeline.GeminiAnalyzer", return_value=MagicMock()), \
         patch("src.core.pipeline.NotificationService", return_value=MagicMock()), \
         patch("src.core.pipeline.MxClient", return_value=MagicMock(enabled=False)), \
         patch("src.core.pipeline.MxSearchAdapter", return_value=MagicMock()):
        return StockAnalysisPipeline(config=config)


def test_pipeline_runs_intel_search_when_only_mx_route_is_available():
    config = _make_config()
    search_service = MagicMock()
    search_service.get_capability_status.return_value = SearchCapabilityStatus(
        legacy_available=False,
        mx_route_available=True,
        comprehensive_intel_available=True,
        reasons=["legacy_providers_unavailable"],
    )
    search_service.search_comprehensive_intel.return_value = {}

    pipeline = _build_pipeline(config)
    pipeline.search_service = search_service
    pipeline.social_sentiment_service = None
    pipeline._emit_progress = MagicMock()
    pipeline._build_query_context = MagicMock(return_value={})
    pipeline._enhance_context = MagicMock(return_value={"realtime": {}})
    pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
    pipeline.fetcher_manager.get_fundamental_context.return_value = {}
    pipeline.fetcher_manager.get_daily_data.return_value = (None, "unit")
    pipeline.fetcher_manager.get_chip_distribution.return_value = None
    pipeline.fetcher_manager.build_failed_fundamental_context.return_value = {}
    pipeline.trend_analyzer.analyze.return_value = _DummyTrendResult()
    pipeline.db.get_data_range.return_value = []
    pipeline.db.get_analysis_context.return_value = {
        "code": "600519",
        "stock_name": "贵州茅台",
        "date": "2026-01-01",
        "today": {},
        "yesterday": {},
    }
    pipeline.analyzer.analyze.return_value = _DummyResult()

    with patch("src.core.pipeline.get_market_for_stock", return_value="cn"), \
         patch("src.core.pipeline.get_market_now") as mock_market_now, \
         patch("src.core.pipeline.fill_chip_structure_if_needed"), \
         patch("src.core.pipeline.fill_price_position_if_needed"), \
         patch("src.core.pipeline.fill_institution_structure_if_needed"), \
         patch(
             "src.core.pipeline.normalize_analysis_result",
             return_value=SimpleNamespace(
                 changed_rules=[],
                 changed_rule_count=0,
                 max_severity="info",
                 reason_codes=[],
                 to_dict=lambda: {"changed_rules": [], "max_severity": "info", "reason_codes": []},
             ),
         ):
        mock_market_now.return_value = SimpleNamespace(date=lambda: __import__("datetime").date(2026, 1, 1))
        result = pipeline.analyze_stock("600519", report_type=SimpleNamespace(value="simple"), query_id="q1")

    assert result is not None
    search_service.search_comprehensive_intel.assert_called_once_with(
        stock_code="600519",
        stock_name="贵州茅台",
        max_searches=5,
    )


def test_pipeline_analyze_stock_reuses_prefetched_daily_data_cache():
    config = _make_config()
    pipeline = _build_pipeline(config)
    pipeline.search_service = None
    pipeline.social_sentiment_service = None
    pipeline._emit_progress = MagicMock()
    pipeline._build_query_context = MagicMock(return_value={})
    pipeline._enhance_context = MagicMock(return_value={"realtime": {}})
    pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
    pipeline.fetcher_manager.get_fundamental_context.return_value = {}
    pipeline.fetcher_manager.get_daily_data.side_effect = AssertionError("should reuse prefetched daily cache")
    pipeline.fetcher_manager.get_chip_distribution.return_value = None
    pipeline.fetcher_manager.build_failed_fundamental_context.return_value = {}
    pipeline.trend_analyzer.analyze.return_value = _DummyTrendResult()
    pipeline.db.get_data_range.return_value = []
    pipeline.db.get_analysis_context.return_value = {
        "code": "600519",
        "stock_name": "贵州茅台",
        "date": "2026-01-01",
        "today": {},
        "yesterday": {},
    }
    pipeline.analyzer.analyze.return_value = _DummyResult()
    pipeline._prefetched_daily_data = {
        "600519": (
            __import__("pandas").DataFrame(
                [{"date": "2026-01-01", "close": 10.0, "open": 9.8, "high": 10.1, "low": 9.7, "volume": 100, "amount": 1000.0, "pct_chg": 0.5}]
            ),
            "TushareFetcher",
            date(2026, 1, 1),
        )
    }
    frozen_time = __import__("datetime").datetime(2026, 1, 1, 9, 30)

    with patch("src.core.pipeline.get_market_for_stock", return_value="cn"), \
         patch("src.core.pipeline.get_market_now") as mock_market_now, \
         patch.object(pipeline, "_resolve_resume_target_date", return_value=date(2026, 1, 1)) as mock_resolve_target_date, \
         patch("src.core.pipeline.fill_chip_structure_if_needed"), \
         patch("src.core.pipeline.fill_price_position_if_needed"), \
         patch("src.core.pipeline.fill_institution_structure_if_needed"), \
         patch(
             "src.core.pipeline.normalize_analysis_result",
             return_value=SimpleNamespace(
                 changed_rules=[],
                 changed_rule_count=0,
                 max_severity="info",
                 reason_codes=[],
                 to_dict=lambda: {"changed_rules": [], "max_severity": "info", "reason_codes": []},
             ),
         ):
        mock_market_now.return_value = SimpleNamespace(date=lambda: __import__("datetime").date(2026, 1, 1))
        result = pipeline.analyze_stock(
            "600519",
            report_type=SimpleNamespace(value="simple"),
            query_id="q1",
            current_time=frozen_time,
        )

    assert result is not None
    pipeline.fetcher_manager.get_daily_data.assert_not_called()
    mock_resolve_target_date.assert_called_once_with("600519", current_time=frozen_time)
