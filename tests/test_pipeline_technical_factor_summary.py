from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from src.core.pipeline import StockAnalysisPipeline


class TestPipelineTechnicalFactorSummary:
    def test_build_technical_factor_summary_for_analysis_calls_fetcher_and_summary(self, monkeypatch):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)

        tushare_fetcher = MagicMock()
        tushare_fetcher.is_available.return_value = True
        tushare_fetcher.get_stock_factor_snapshot.return_value = pd.DataFrame(
            [
                {
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

        pipeline.fetcher_manager = SimpleNamespace(
            get_fetcher=lambda name: tushare_fetcher if name == "TushareFetcher" else None
        )

        fake_summary = {"states": {"trend_state": "below_ma20_above_ma60"}}
        monkeypatch.setattr(
            "src.core.pipeline.summarize_stk_factor_snapshot",
            lambda snapshot, close_price=None: fake_summary,
        )

        daily_data = pd.DataFrame([{"trade_date": "20260417", "close": 11.01}])

        result = pipeline._build_technical_factor_summary_for_analysis(
            stock_code="000001",
            daily_data=daily_data,
        )

        assert result == fake_summary
        tushare_fetcher.get_stock_factor_snapshot.assert_called_once_with(
            stock_code="000001",
            start_date="20260417",
            end_date="20260417",
        )

    def test_build_technical_factor_summary_for_analysis_returns_none_when_daily_data_empty(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.fetcher_manager = SimpleNamespace(get_fetcher=lambda name: None)

        result = pipeline._build_technical_factor_summary_for_analysis(
            stock_code="000001",
            daily_data=pd.DataFrame(),
        )

        assert result is None

    def test_build_technical_factor_summary_for_analysis_returns_none_when_tushare_missing(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.fetcher_manager = SimpleNamespace(get_fetcher=lambda name: None)

        daily_data = pd.DataFrame([{"trade_date": "20260417", "close": 11.01}])
        result = pipeline._build_technical_factor_summary_for_analysis(
            stock_code="000001",
            daily_data=daily_data,
        )

        assert result is None

    def test_build_technical_factor_summary_for_analysis_returns_none_when_tushare_unavailable(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        tushare_fetcher = MagicMock()
        tushare_fetcher.is_available.return_value = False
        pipeline.fetcher_manager = SimpleNamespace(
            get_fetcher=lambda name: tushare_fetcher if name == "TushareFetcher" else None
        )

        daily_data = pd.DataFrame([{"trade_date": "20260417", "close": 11.01}])
        result = pipeline._build_technical_factor_summary_for_analysis(
            stock_code="000001",
            daily_data=daily_data,
        )

        assert result is None

    def test_build_technical_factor_summary_for_analysis_uses_date_column_fallback(self, monkeypatch):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        tushare_fetcher = MagicMock()
        tushare_fetcher.is_available.return_value = True
        tushare_fetcher.get_stock_factor_snapshot.return_value = pd.DataFrame([
            {"trade_date": "20260417", "macd_qfq": 0.1}
        ])
        pipeline.fetcher_manager = SimpleNamespace(
            get_fetcher=lambda name: tushare_fetcher if name == "TushareFetcher" else None
        )
        monkeypatch.setattr(
            "src.core.pipeline.summarize_stk_factor_snapshot",
            lambda snapshot, close_price=None: {"ok": True, "close": close_price},
        )

        daily_data = pd.DataFrame([{"date": "2026-04-17", "close": 11.01}])
        result = pipeline._build_technical_factor_summary_for_analysis("000001", daily_data)

        assert result == {"ok": True, "close": 11.01}
        tushare_fetcher.get_stock_factor_snapshot.assert_called_once_with(
            stock_code="000001",
            start_date="20260417",
            end_date="20260417",
        )

    def test_enhance_context_preserves_injected_technical_factor_summary(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.config = SimpleNamespace(mx_enabled=False, report_language="zh")
        pipeline.candidate_enrichment_service = None
        pipeline.fetcher_manager = SimpleNamespace(
            build_failed_fundamental_context=lambda code, reason: {"code": code, "status": "failed", "reason": reason}
        )
        pipeline.search_service = SimpleNamespace(news_window_days=3)

        context = {
            "code": "000001",
            "stock_name": "平安银行",
            "today": {},
            "yesterday": {},
            "technical_factor_summary": {"states": {"trend_state": "below_ma20_above_ma60"}},
        }

        enhanced = pipeline._enhance_context(
            context,
            realtime_quote=None,
            chip_data=None,
            trend_result=None,
            stock_name="平安银行",
            fundamental_context={"status": "ok"},
            portfolio_context=None,
        )

        assert enhanced["technical_factor_summary"]["states"]["trend_state"] == "below_ma20_above_ma60"
