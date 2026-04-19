import pandas as pd
import requests
from unittest.mock import MagicMock

from data_provider.base import RateLimitError
from data_provider.tushare_fetcher import TushareFetcher, DEFAULT_STK_FACTOR_PRO_FIELDS


class TestTushareFetcherStkFactorPro:
    def test_get_stock_factor_snapshot_calls_stk_factor_pro_with_default_fields(self):
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._api = MagicMock()
        fetcher._convert_stock_code = MagicMock(return_value="000001.SZ")
        fetcher._normalize_tushare_date = MagicMock(side_effect=lambda x: x.replace("-", ""))
        fetcher._call_api_with_rate_limit = MagicMock(
            return_value=pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260417",
                        "turnover_rate": 2.1,
                        "volume_ratio": 0.8,
                        "updays": 0,
                        "downdays": 2,
                        "ma_qfq_20": 11.2,
                        "ma_qfq_60": 10.8,
                        "macd_qfq": -0.1,
                        "rsi_qfq_12": 42.0,
                        "boll_mid_qfq": 11.0,
                        "atr_qfq": 0.22,
                    }
                ]
            )
        )

        df = fetcher.get_stock_factor_snapshot(
            stock_code="000001",
            start_date="2026-04-17",
            end_date="2026-04-17",
        )

        assert df is not None
        fetcher._call_api_with_rate_limit.assert_called_once_with(
            "stk_factor_pro",
            ts_code="000001.SZ",
            start_date="20260417",
            end_date="20260417",
            fields=DEFAULT_STK_FACTOR_PRO_FIELDS,
        )

    def test_get_stock_factor_snapshot_normalizes_dates(self):
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._api = MagicMock()
        fetcher._convert_stock_code = MagicMock(return_value="600519.SH")
        fetcher._normalize_tushare_date = MagicMock(side_effect=lambda x: x.replace("-", ""))
        fetcher._call_api_with_rate_limit = MagicMock(return_value=pd.DataFrame())

        fetcher.get_stock_factor_snapshot(
            stock_code="600519",
            start_date="2026-04-01",
            end_date="2026-04-17",
        )

        kwargs = fetcher._call_api_with_rate_limit.call_args.kwargs
        assert kwargs["start_date"] == "20260401"
        assert kwargs["end_date"] == "20260417"

    def test_get_stock_factor_snapshot_returns_none_on_empty_df(self):
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._api = MagicMock()
        fetcher._convert_stock_code = MagicMock(return_value="000001.SZ")
        fetcher._normalize_tushare_date = MagicMock(side_effect=lambda x: x)
        fetcher._call_api_with_rate_limit = MagicMock(return_value=pd.DataFrame())

        result = fetcher.get_stock_factor_snapshot(
            stock_code="000001",
            start_date="20260417",
            end_date="20260417",
        )

        assert result is None

    def test_get_stock_factor_snapshot_keeps_whitelist_fields_and_casts_types(self):
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._api = MagicMock()
        fetcher._convert_stock_code = MagicMock(return_value="000001.SZ")
        fetcher._normalize_tushare_date = MagicMock(side_effect=lambda x: x)
        fetcher._call_api_with_rate_limit = MagicMock(
            return_value=pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260417",
                        "turnover_rate": "2.1",
                        "volume_ratio": "0.8",
                        "updays": "0",
                        "downdays": "2",
                        "ma_qfq_20": "11.2",
                        "ma_qfq_60": "10.8",
                        "macd_qfq": "-0.1",
                        "rsi_qfq_12": "42.0",
                        "boll_mid_qfq": "11.0",
                        "atr_qfq": "0.22",
                        "unexpected_field": "boom",
                    }
                ]
            )
        )

        df = fetcher.get_stock_factor_snapshot(
            stock_code="000001",
            start_date="20260417",
            end_date="20260417",
        )

        assert df is not None
        assert "unexpected_field" not in df.columns
        assert float(df.iloc[0]["turnover_rate"]) == 2.1
        assert float(df.iloc[0]["macd_qfq"]) == -0.1
        assert str(df.iloc[0]["trade_date"]) == "20260417"

    def test_get_stock_factor_snapshot_returns_none_when_api_unavailable(self):
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._api = None

        result = fetcher.get_stock_factor_snapshot(
            stock_code="000001",
            start_date="20260417",
            end_date="20260417",
        )

        assert result is None

    def test_get_stock_factor_snapshot_returns_none_on_non_rate_limit_error(self):
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._api = MagicMock()
        fetcher._convert_stock_code = MagicMock(return_value="000001.SZ")
        fetcher._normalize_tushare_date = MagicMock(side_effect=lambda x: x)
        fetcher._call_api_with_rate_limit = MagicMock(side_effect=Exception("boom"))
        fetcher._temporary_quota_blocks = {}

        result = fetcher.get_stock_factor_snapshot(
            stock_code="000001",
            start_date="20260417",
            end_date="20260417",
        )

        assert result is None
        assert "stk_factor_pro" not in fetcher._temporary_quota_blocks

    def test_get_stock_factor_snapshot_reraises_rate_limit_error(self):
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._api = MagicMock()
        fetcher._convert_stock_code = MagicMock(return_value="000001.SZ")
        fetcher._normalize_tushare_date = MagicMock(side_effect=lambda x: x)
        fetcher._call_api_with_rate_limit = MagicMock(side_effect=RateLimitError("quota"))

        try:
            fetcher.get_stock_factor_snapshot(
                stock_code="000001",
                start_date="20260417",
                end_date="20260417",
            )
            assert False, "expected RateLimitError"
        except RateLimitError:
            assert True

    def test_get_stock_factor_snapshot_records_temporary_block_on_timeout(self):
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._api = MagicMock()
        fetcher._convert_stock_code = MagicMock(return_value="000001.SZ")
        fetcher._normalize_tushare_date = MagicMock(side_effect=lambda x: x)
        fetcher._call_api_with_rate_limit = MagicMock(
            side_effect=requests.exceptions.ReadTimeout("read timed out")
        )
        fetcher._temporary_quota_blocks = {}
        fetcher._temporary_quota_block_seconds = 600.0

        result = fetcher.get_stock_factor_snapshot(
            stock_code="000001",
            start_date="20260417",
            end_date="20260417",
        )

        assert result is None
        assert "stk_factor_pro" in fetcher._temporary_quota_blocks
        reason, blocked_until = fetcher._temporary_quota_blocks["stk_factor_pro"]
        assert "timeout" in reason.lower()
        assert blocked_until > 0

    def test_get_stock_factor_snapshot_short_circuits_when_temporarily_blocked(self):
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._api = MagicMock()
        fetcher._convert_stock_code = MagicMock(return_value="000001.SZ")
        fetcher._normalize_tushare_date = MagicMock(side_effect=lambda x: x)
        fetcher._call_api_with_rate_limit = MagicMock(side_effect=AssertionError("should not call api"))
        fetcher._temporary_quota_blocks = {"stk_factor_pro": ("previous timeout", 10**12)}

        try:
            fetcher.get_stock_factor_snapshot(
                stock_code="000001",
                start_date="20260417",
                end_date="20260417",
            )
            assert False, "expected RateLimitError"
        except RateLimitError as exc:
            assert "temporary quota block" in str(exc).lower()
        fetcher._call_api_with_rate_limit.assert_not_called()
