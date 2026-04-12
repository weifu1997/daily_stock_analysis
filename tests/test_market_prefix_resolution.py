# -*- coding: utf-8 -*-

from data_provider.baostock_fetcher import BaostockFetcher
from data_provider.yfinance_fetcher import YfinanceFetcher


def test_baostock_convert_stock_code_recognizes_605_and_001_prefixes():
    fetcher = BaostockFetcher()
    assert fetcher._convert_stock_code("605050") == "sh.605050"
    assert fetcher._convert_stock_code("001696") == "sz.001696"


def test_yfinance_convert_stock_code_recognizes_605_and_001_prefixes():
    fetcher = YfinanceFetcher()
    assert fetcher._convert_stock_code("605050") == "605050.SS"
    assert fetcher._convert_stock_code("001696") == "001696.SZ"
