from src.analyzer import GeminiAnalyzer


def test_ma60_fallback_appends_ma60_when_missing():
    analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
    text = analyzer._ensure_ma60_in_ma_analysis(
        "均线走平，短线震荡。",
        {"ma60": 10.8, "ma20": 11.2, "close": 11.0},
        {"ma_status": "多头排列"},
    )
    assert "MA60" in text
    assert "中期" in text or "趋势" in text


def test_ma60_fallback_keeps_existing_explicit_ma60_text():
    analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
    text = analyzer._ensure_ma60_in_ma_analysis(
        "MA60 上方，趋势偏强。",
        {"ma60": 10.8, "ma20": 11.2, "close": 11.0},
        {"ma_status": "多头排列"},
    )
    assert text == "MA60 上方，趋势偏强。"
