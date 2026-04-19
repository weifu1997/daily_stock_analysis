# -*- coding: utf-8 -*-
"""Regression tests for TushareFetcher HTTP client initialization."""

import importlib.util
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    json_repair_available = importlib.util.find_spec("json_repair") is not None
except ValueError:
    json_repair_available = "json_repair" in sys.modules

if not json_repair_available and "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.tushare_fetcher import TushareFetcher, _TushareHttpClient
import pandas as pd


class TestTushareHttpClient(unittest.TestCase):
    """Ensure the lightweight HTTP client preserves Tushare Pro request semantics."""

    def test_query_posts_to_official_pro_endpoint(self) -> None:
        client = _TushareHttpClient(token="demo-token", timeout=15)
        response = MagicMock(
            status_code=200,
            text=json.dumps(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code", "close"],
                        "items": [["600519.SH", 1688.0]],
                    },
                }
            ),
        )

        with patch("data_provider.tushare_fetcher.requests.post", return_value=response) as post_mock:
            df = client.daily(ts_code="600519.SH", start_date="20260320", end_date="20260325")

        post_mock.assert_called_once_with(
            "http://api.tushare.pro",
            json={
                "api_name": "daily",
                "token": "demo-token",
                "params": {
                    "ts_code": "600519.SH",
                    "start_date": "20260320",
                    "end_date": "20260325",
                },
                "fields": "",
            },
            timeout=15,
        )
        self.assertEqual(df.to_dict(orient="records"), [{"ts_code": "600519.SH", "close": 1688.0}])

    def test_query_posts_to_configured_proxy_endpoint(self) -> None:
        client = _TushareHttpClient(
            token="demo-token",
            timeout=15,
            api_url="https://tushare.data.godscode.com.cn",
        )
        response = MagicMock(
            status_code=200,
            text=json.dumps(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code", "close"],
                        "items": [["000001.SZ", 12.34]],
                    },
                }
            ),
        )

        with patch("data_provider.tushare_fetcher.requests.post", return_value=response) as post_mock:
            df = client.daily(ts_code="000001.SZ", start_date="20260320", end_date="20260325")

        post_mock.assert_called_once_with(
            "https://tushare.data.godscode.com.cn",
            json={
                "api_name": "daily",
                "token": "demo-token",
                "params": {
                    "ts_code": "000001.SZ",
                    "start_date": "20260320",
                    "end_date": "20260325",
                },
                "fields": "",
            },
            timeout=15,
        )
        self.assertEqual(df.to_dict(orient="records"), [{"ts_code": "000001.SZ", "close": 12.34}])


class TestTushareFetcherInit(unittest.TestCase):
    """Ensure fetcher initialization no longer depends on the tushare SDK package."""

    def test_init_builds_http_client_when_token_present(self) -> None:
        config = SimpleNamespace(tushare_token="demo-token", tushare_api_url=None)

        with patch("data_provider.tushare_fetcher.get_config", return_value=config):
            fetcher = TushareFetcher()

        self.assertIsInstance(fetcher._api, _TushareHttpClient)
        self.assertTrue(fetcher.is_available())
        self.assertEqual(fetcher.priority, -1)

    def test_init_uses_configured_proxy_url_when_present(self) -> None:
        config = SimpleNamespace(
            tushare_token="demo-token",
            tushare_api_url="https://tushare.data.godscode.com.cn",
        )

        with patch("data_provider.tushare_fetcher.get_config", return_value=config):
            fetcher = TushareFetcher()

        self.assertIsInstance(fetcher._api, _TushareHttpClient)
        self.assertEqual(fetcher._api._api_url, "https://tushare.data.godscode.com.cn")

    def test_http_client_marks_pro_bar_as_unsupported(self) -> None:
        client = _TushareHttpClient(token="demo-token")

        self.assertFalse(client.supports_pro_bar)

    def test_daily_adj_data_skips_pro_bar_warning_for_http_client(self) -> None:
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._api = _TushareHttpClient(token="demo-token")
        fetcher._convert_stock_code = MagicMock(return_value="600519.SH")
        raw_df = pd.DataFrame(
            {
                "trade_date": ["20260325"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.5],
                "close": [10.5],
                "vol": [1.2],
                "amount": [2.3],
                "pct_chg": [1.5],
            }
        )
        factor_df = pd.DataFrame(
            {
                "trade_date": ["20260325"],
                "adj_factor": [1.234],
            }
        )
        fetcher._fetch_raw_data = MagicMock(return_value=raw_df)
        fetcher.get_adj_factor_data = MagicMock(return_value=factor_df)

        with patch("data_provider.tushare_fetcher.logger.warning") as warning_mock:
            result = fetcher.get_daily_adj_data("600519", "2026-03-20", "2026-03-25")

        self.assertIsNotNone(result)
        if result is None:
            self.fail("expected adjusted daily data")
        self.assertEqual(result["adj_factor"].iloc[0], 1.234)
        warning_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
