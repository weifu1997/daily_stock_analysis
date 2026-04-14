# -*- coding: utf-8 -*-
"""Regression tests for mx-search primary routing and fallback."""

import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import SearchResponse, SearchResult, SearchService


class TestMxSearchRouting(unittest.TestCase):
    def _make_config(self, **overrides):
        base = {
            "mx_enabled": True,
            "mx_search_primary_provider": "mx",
            "mx_search_fallback_enabled": True,
            "mx_search_min_results": 2,
            "mx_search_route_timeout_seconds": 1.0,
            "mx_timeout_seconds": 1.0,
            "mx_base_url": "https://mx.example.com",
            "mx_api_key": "dummy",
            "news_max_age_days": 3,
            "news_strategy_profile": "short",
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def _make_mx_resp(self, items, ok=True, error=None):
        return SimpleNamespace(ok=ok, data={"items": items}, error=error)

    @patch("src.search_service.get_config")
    def test_mx_hit_returns_primary_results(self, mock_get_config):
        mock_get_config.return_value = self._make_config()
        mx_client = MagicMock()
        mx_client.enabled = True
        mx_client.search.return_value = self._make_mx_resp(
            [
                {
                    "title": "茅台发布新消息",
                    "summary": "摘要",
                    "url": "https://example.com/a",
                    "source": "mx",
                    "published_at": datetime.now().date().isoformat(),
                },
                {
                    "title": "茅台行业跟踪",
                    "summary": "摘要2",
                    "url": "https://example.com/b",
                    "source": "mx",
                    "published_at": datetime.now().date().isoformat(),
                },
            ]
        )
        with patch("src.search_service.MxClient", return_value=mx_client):
            service = SearchService(
                bocha_keys=["dummy"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )

        resp = service.search_stock_news("600519", "贵州茅台", max_results=2)
        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "mx-search")
        self.assertEqual(len(resp.results), 2)
        mx_client.search.assert_called()

    @patch("src.search_service.get_config")
    def test_mx_insufficient_results_falls_back_to_legacy_provider(self, mock_get_config):
        mock_get_config.return_value = self._make_config(mx_search_min_results=3)
        mx_client = MagicMock()
        mx_client.enabled = True
        mx_client.search.return_value = self._make_mx_resp(
            [
                {
                    "title": "仅一条结果",
                    "summary": "摘要",
                    "url": "https://example.com/a",
                    "source": "mx",
                    "published_at": datetime.now().date().isoformat(),
                }
            ]
        )
        fallback_response = SearchResponse(
            query="贵州茅台 600519 股票 最新消息",
            results=[
                SearchResult(
                    title="旧provider结果",
                    snippet="snippet",
                    url="https://example.com/fallback",
                    source="legacy",
                    published_date=datetime.now().date().isoformat(),
                )
            ],
            provider="Tavily",
            success=True,
        )
        with patch("src.search_service.MxClient", return_value=mx_client):
            service = SearchService(
                bocha_keys=["dummy"],
                tavily_keys=["dummy"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )
            service._providers = [SimpleNamespace(is_available=True, name="Tavily", search=MagicMock(return_value=fallback_response))]

        resp = service.search_stock_news("600519", "贵州茅台", max_results=1)
        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "Tavily")
        self.assertEqual([r.title for r in resp.results], ["旧provider结果"])

    @patch("src.search_service.get_config")
    def test_us_stock_never_uses_mx_primary(self, mock_get_config):
        mock_get_config.return_value = self._make_config()
        mx_client = MagicMock()
        mx_client.enabled = True
        with patch("src.search_service.MxClient", return_value=mx_client):
            service = SearchService(
                bocha_keys=["dummy"],
                tavily_keys=["dummy"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )

        self.assertFalse(service._should_use_mx_primary_for_stock_news("AAPL"))
        self.assertFalse(service._should_use_mx_primary_for_stock_news("TSLA"))

    @patch("src.search_service.get_config")
    def test_non_financial_query_directly_uses_fallback(self, mock_get_config):
        mock_get_config.return_value = self._make_config()
        mx_client = MagicMock()
        mx_client.enabled = True
        mx_client.search.return_value = self._make_mx_resp([])
        fallback_response = SearchResponse(
            query="best laptop review",
            results=[
                SearchResult(
                    title="Laptop review",
                    snippet="snippet",
                    url="https://example.com/laptop",
                    source="legacy",
                    published_date=datetime.now().date().isoformat(),
                )
            ],
            provider="SerpAPI",
            success=True,
        )
        with patch("src.search_service.MxClient", return_value=mx_client):
            service = SearchService(
                bocha_keys=["dummy"],
                serpapi_keys=["dummy"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )
            service._providers = [SimpleNamespace(is_available=True, name="SerpAPI", search=MagicMock(return_value=fallback_response))]

        mx_resp = service._mx_search_with_timeout(
            "best laptop review",
            max_results=3,
            days=3,
            route_label="test",
        )
        self.assertFalse(mx_resp.success)
        self.assertEqual(mx_resp.error_message, "non_financial_query")

    @patch("src.search_service.get_config")
    def test_comprehensive_intel_uses_mx_hit_before_fallback(self, mock_get_config):
        mock_get_config.return_value = self._make_config(mx_search_min_results=2)
        today = datetime.now().date().isoformat()
        mx_client = MagicMock()
        mx_client.enabled = True
        mx_client.search.side_effect = [
            self._make_mx_resp(
                [
                    {
                        "title": "茅台最新动态",
                        "summary": "摘要1",
                        "url": "https://example.com/news-a",
                        "source": "mx",
                        "published_at": today,
                    },
                    {
                        "title": "茅台公告更新",
                        "summary": "摘要2",
                        "url": "https://example.com/news-b",
                        "source": "mx",
                        "published_at": today,
                    },
                ]
            ),
            self._make_mx_resp(
                [
                    {
                        "title": "茅台研报跟踪",
                        "summary": "摘要3",
                        "url": "https://example.com/analysis-a",
                        "source": "mx",
                        "published_at": today,
                    },
                    {
                        "title": "茅台机构点评",
                        "summary": "摘要4",
                        "url": "https://example.com/analysis-b",
                        "source": "mx",
                        "published_at": today,
                    },
                ]
            ),
        ]
        fallback_provider = SimpleNamespace(
            is_available=True,
            name="Tavily",
            search=MagicMock(side_effect=AssertionError("fallback should not run when mx hit is enough")),
        )
        with patch("src.search_service.MxClient", return_value=mx_client):
            service = SearchService(
                bocha_keys=["dummy"],
                tavily_keys=["dummy"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )
            service._providers = [fallback_provider]

        with patch("src.search_service.time.sleep"):
            intel = service.search_comprehensive_intel("600519", "贵州茅台", max_searches=2)

        self.assertEqual([item.title for item in intel["latest_news"].results], ["茅台最新动态", "茅台公告更新"])
        self.assertEqual([item.title for item in intel["market_analysis"].results], ["茅台研报跟踪", "茅台机构点评"])
        self.assertEqual(mx_client.search.call_count, 2)
        fallback_provider.search.assert_not_called()

    @patch("src.search_service.get_config")
    def test_comprehensive_intel_respects_mx_fallback_disabled_on_insufficient_results(self, mock_get_config):
        mock_get_config.return_value = self._make_config(
            mx_search_min_results=3,
            mx_search_fallback_enabled=False,
        )
        today = datetime.now().date().isoformat()
        mx_client = MagicMock()
        mx_client.enabled = True
        mx_client.search.side_effect = [
            self._make_mx_resp(
                [
                    {
                        "title": "旧消息",
                        "summary": "摘要1",
                        "url": "https://example.com/news-old",
                        "source": "mx",
                        "published_at": "2020-01-01",
                    },
                    {
                        "title": "新消息",
                        "summary": "摘要2",
                        "url": "https://example.com/news-new",
                        "source": "mx",
                        "published_at": today,
                    },
                ]
            ),
            self._make_mx_resp(
                [
                    {
                        "title": "无日期研报",
                        "summary": "摘要3",
                        "url": "https://example.com/analysis-unknown",
                        "source": "mx",
                        "published_at": None,
                    }
                ]
            ),
        ]
        fallback_provider = SimpleNamespace(
            is_available=True,
            name="Tavily",
            search=MagicMock(side_effect=AssertionError("fallback should stay disabled")),
        )
        with patch("src.search_service.MxClient", return_value=mx_client):
            service = SearchService(
                bocha_keys=["dummy"],
                tavily_keys=["dummy"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )
            service._providers = [fallback_provider]

        with patch("src.search_service.time.sleep"):
            intel = service.search_comprehensive_intel("600519", "贵州茅台", max_searches=2)

        self.assertEqual([item.title for item in intel["latest_news"].results], ["新消息"])
        self.assertEqual([item.title for item in intel["market_analysis"].results], ["无日期研报"])
        self.assertIsNone(intel["market_analysis"].results[0].published_date)
        fallback_provider.search.assert_not_called()

    @patch("src.search_service.get_config")
    def test_comprehensive_intel_preserves_historical_fallback_order(self, mock_get_config):
        mock_get_config.return_value = self._make_config(mx_enabled=False)
        calls = []

        def make_provider(name, success=False):
            def _search(*_args, **_kwargs):
                calls.append(name)
                return SearchResponse(
                    query="贵州茅台 600519 最新 新闻 重大 事件",
                    results=(
                        [
                            SearchResult(
                                title=f"{name}-result",
                                snippet="snippet",
                                url=f"https://example.com/{name}",
                                source=name,
                                published_date=datetime.now().date().isoformat(),
                            )
                        ]
                        if success else []
                    ),
                    provider=name,
                    success=success,
                    error_message=None if success else f"{name}-empty",
                )
            return SimpleNamespace(is_available=True, name=name, search=MagicMock(side_effect=_search))

        bocha = make_provider("Bocha")
        brave = make_provider("Brave", success=True)
        serpapi = make_provider("SerpAPI", success=True)

        with patch("src.search_service.MxClient"):
            service = SearchService(
                bocha_keys=["dummy"],
                brave_keys=["dummy"],
                serpapi_keys=["dummy"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )
            service._providers = [bocha, brave, serpapi]

        with patch("src.search_service.time.sleep"):
            intel = service.search_comprehensive_intel("600519", "贵州茅台", max_searches=1)

        # P0-1: 统一 provider 顺序后，fallback 遍历 self._providers = [bocha, brave, serpapi]
        # bocha (success=False) 先被调用，然后 brave (success=True) 命中
        self.assertEqual(calls, ["Bocha", "Brave"])
        self.assertEqual([item.title for item in intel["latest_news"].results], ["Brave-result"])
        serpapi.search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
