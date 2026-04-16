# -*- coding: utf-8 -*-

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.search_service import SearchResponse, SearchResult, SearchService


class TestSearchComprehensiveIntelResilience:
    def _make_config(self, **overrides):
        base = {
            "mx_enabled": False,
            "mx_search_primary_provider": "mx",
            "mx_search_fallback_enabled": True,
            "mx_search_min_results": 2,
            "mx_search_route_timeout_seconds": 1.0,
            "mx_timeout_seconds": 1.0,
            "mx_base_url": None,
            "mx_api_key": None,
            "news_max_age_days": 3,
            "news_strategy_profile": "short",
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    @patch("src.search_service.get_config")
    def test_none_dimension_response_does_not_stop_following_dimensions(self, mock_get_config):
        mock_get_config.return_value = self._make_config()
        with patch("src.search_service.MxClient") as mock_mx_client:
            mock_mx_client.return_value.enabled = False
            service = SearchService(
                tavily_keys=["dummy"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )

        ok_response = SearchResponse(
            query="贵州茅台 600519 机构观点 研报 目标价",
            results=[
                SearchResult(
                    title="机构维度结果",
                    snippet="snippet",
                    url="https://example.com/intel",
                    source="legacy",
                    published_date=datetime.now().date().isoformat(),
                )
            ],
            provider="Tavily",
            success=True,
        )

        with patch.object(
            service,
            "_search_dimension_with_fallback",
            side_effect=[None, ok_response, ok_response, ok_response, ok_response, ok_response],
        ) as mock_dimension_search:
            intel = service.search_comprehensive_intel("600519", "贵州茅台", max_searches=3)

        assert mock_dimension_search.call_count >= 3
        assert "latest_news" not in intel
        assert "market_analysis" in intel
        assert "risk_check" in intel
