from types import SimpleNamespace
from unittest.mock import MagicMock

from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType


def test_analyze_stock_injects_candidate_source_into_context_snapshot():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline._emit_progress = lambda *args, **kwargs: None
    pipeline._collect_analysis_inputs = MagicMock(return_value=SimpleNamespace(stock_name="测试股"))
    pipeline._should_use_agent = MagicMock(return_value=False)
    pipeline._run_traditional_analysis = MagicMock(return_value="ok")
    pipeline._candidate_source_map = {
        "600519": {
            "code": "600519",
            "candidate_source": "mx_preselect",
            "source_query": "A股 正常交易 低估值 高ROE",
            "source_profile": "env_query",
            "source_rank": 1,
            "pool_reason": "mx_xuangu_selected",
            "forced_by_portfolio": False,
            "fallback_used": False,
        }
    }

    result = pipeline.analyze_stock("600519", ReportType.SIMPLE, query_id="qid-1")

    assert result == "ok"
    args, _kwargs = pipeline._run_traditional_analysis.call_args
    inputs = args[3]
    assert inputs.candidate_source == {
        "code": "600519",
        "candidate_source": "mx_preselect",
        "source_query": "A股 正常交易 低估值 高ROE",
        "source_profile": "env_query",
        "source_rank": 1,
        "pool_reason": "mx_xuangu_selected",
        "forced_by_portfolio": False,
        "fallback_used": False,
    }


def test_build_context_snapshot_persists_candidate_source_map_and_current_source():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline._candidate_source_map = {
        "600519": {"code": "600519", "candidate_source": "mx_preselect"},
        "000776": {"code": "000776", "candidate_source": "portfolio"},
    }

    snapshot = pipeline._build_context_snapshot(
        enhanced_context={
            "candidate_source": {"code": "600519", "candidate_source": "mx_preselect"},
            "stock_name": "测试股",
        },
        news_content="news",
        realtime_quote=None,
        chip_data=None,
    )

    assert snapshot["candidate_source_map"] == pipeline._candidate_source_map
    assert snapshot["candidate_source"] == {"code": "600519", "candidate_source": "mx_preselect"}
