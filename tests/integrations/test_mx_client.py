# -*- coding: utf-8 -*-

import logging
import sys
from unittest.mock import MagicMock, patch

import requests

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.integrations.mx.client import MxClient
from src.integrations.mx.search_adapter import MxSearchAdapter
from src.services.candidate_enrichment import CandidateEnrichmentService


class TestMxClient:
    def test_disabled_client_returns_error(self):
        with patch("src.integrations.mx.client.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(mx_base_url="", mx_api_key=None, mx_timeout_seconds=1.0)
            client = MxClient()
        resp = client.healthcheck()
        assert not resp.ok
        assert resp.error == "mx_disabled"

    def test_timeout_logs_info_instead_of_warning(self, caplog):
        with patch("src.integrations.mx.client.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                mx_base_url="https://mx.example.com",
                mx_api_key="dummy",
                mx_timeout_seconds=1.0,
            )
            client = MxClient()

        with patch("src.integrations.mx.client.requests.post", side_effect=requests.Timeout("read timed out")):
            with caplog.at_level(logging.INFO):
                resp = client.search("贵州茅台 最新消息")

        assert not resp.ok
        assert "read timed out" in resp.error
        assert any(record.levelno == logging.INFO and "mx request timed out" in record.message for record in caplog.records)
        assert all(record.levelno < logging.WARNING for record in caplog.records if "mx request" in record.message)

    def test_search_adapter_transforms_items(self):
        fake_client = MagicMock()
        fake_client.enabled = True
        fake_client.search.return_value = MagicMock(
            ok=True,
            data={
                "items": [
                    {
                        "title": "公告",
                        "summary": "利好",
                        "code": "600519",
                        "source": "mx",
                        "url": "https://example.com",
                        "published_at": "2026-04-06",
                        "tags": ["theme"],
                        "risk_flags": ["risk"],
                    }
                ]
            },
            error=None,
        )
        adapter = MxSearchAdapter(fake_client)
        signal = adapter.enrich_stock("600519", "贵州茅台")
        assert signal.code == "600519"
        assert signal.event_score > 0
        assert signal.theme_tags == ["theme"]
        assert signal.risk_flags == ["risk"]
        assert len(signal.events) == 1

    def test_candidate_enrichment_builds_summary(self):
        fake_adapter = MagicMock()
        fake_adapter.enrich_stock.return_value = MagicMock(
            code="600519",
            name="贵州茅台",
            event_score=10.0,
            theme_tags=["白酒"],
            risk_flags=[],
            events=[],
        )
        svc = CandidateEnrichmentService(fake_adapter)
        summary = svc.build_report_summary("600519", "贵州茅台")
        assert summary["mx_enabled"] is True
        assert summary["mx_event_score"] == 10.0
        assert summary["mx_theme_tags"] == ["白酒"]
