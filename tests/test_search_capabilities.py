# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.search_service import SearchService


class TestSearchCapabilities:
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

    @patch("src.search_service.get_config")
    def test_mx_only_configuration_is_explicitly_reported(self, mock_get_config):
        mock_get_config.return_value = self._make_config()
        with patch("src.search_service.MxClient") as mock_mx_client:
            mock_mx_client.return_value.enabled = True
            service = SearchService(
                bocha_keys=[],
                tavily_keys=[],
                brave_keys=[],
                serpapi_keys=[],
                minimax_keys=[],
                searxng_base_urls=[],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )

        capability = service.get_capability_status()
        assert service.is_available is False
        assert capability.legacy_available is False
        assert capability.mx_route_available is True
        assert capability.comprehensive_intel_available is True
        assert "legacy_providers_unavailable" in capability.reasons
        assert service.can_search_stock_news("600519") is True
        assert service.can_search_comprehensive_intel("600519") is True

    @patch("src.search_service.get_config")
    def test_legacy_provider_availability_remains_true_when_provider_exists(self, mock_get_config):
        mock_get_config.return_value = self._make_config(mx_enabled=False)
        with patch("src.search_service.MxClient") as mock_mx_client:
            mock_mx_client.return_value.enabled = False
            service = SearchService(
                bocha_keys=["dummy"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )

        capability = service.get_capability_status()
        assert capability.legacy_available is True
        assert capability.mx_route_available is False
        assert capability.comprehensive_intel_available is True
        assert service.is_available is True

    @patch("src.search_service.get_config")
    def test_capability_reasons_explain_fully_unavailable_state(self, mock_get_config):
        mock_get_config.return_value = self._make_config(mx_enabled=False)
        with patch("src.search_service.MxClient") as mock_mx_client:
            mock_mx_client.return_value.enabled = False
            service = SearchService(
                bocha_keys=[],
                tavily_keys=[],
                brave_keys=[],
                serpapi_keys=[],
                minimax_keys=[],
                searxng_base_urls=[],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )

        capability = service.get_capability_status()
        assert capability.legacy_available is False
        assert capability.mx_route_available is False
        assert capability.comprehensive_intel_available is False
        assert "legacy_providers_unavailable" in capability.reasons
        assert "mx_disabled" in capability.reasons
        assert "comprehensive_intel_unavailable" in capability.reasons
        assert service.can_search_stock_news("600519") is False
        assert service.can_search_comprehensive_intel("600519") is False
