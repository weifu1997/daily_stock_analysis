from src.analyzer import GeminiAnalyzer


def test_format_prompt_requires_explicit_ma60_rule():
    analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
    prompt = analyzer._format_prompt(
        {
            "code": "603166",
            "date": "2026-04-22",
            "today": {
                "close": 11.0,
                "open": 10.8,
                "high": 11.2,
                "low": 10.6,
                "pct_chg": 1.2,
                "volume": 123456,
                "amount": 7890000,
                "ma5": 10.9,
                "ma10": 10.85,
                "ma20": 10.7,
                "ma60": 10.4,
            },
            "ma_status": "多头排列",
        },
        "福达股份",
        report_language="zh",
    )
    assert "MA60" in prompt
    assert "均线输出硬约束" in prompt
    assert "必须显式提到 MA60" in prompt
    assert "MA20 与 MA60" in prompt or "MA20与MA60" in prompt
