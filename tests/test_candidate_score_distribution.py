from src.analysis.candidate_layers.distribution import (
    assign_l2_score_bucket,
    build_l2_report_summary,
    summarize_l2_score_distribution,
)


def test_assign_l2_score_bucket_uses_stable_ranges():
    assert assign_l2_score_bucket(None) == "missing"
    assert assign_l2_score_bucket(5) == "<6"
    assert assign_l2_score_bucket(6) == "6-9"
    assert assign_l2_score_bucket(10) == "10-13"
    assert assign_l2_score_bucket(14) == "14-17"
    assert assign_l2_score_bucket(18) == "18+"


def _sample_rows():
    return [
        {
            "code": "601298.SH",
            "name": "青岛港",
            "score": 18,
            "rating": "★★★★★ 强烈推荐",
            "trade_bias": "right_side_candidate",
            "risk_flags": [],
            "factor_scores": {"valuation": 6, "quality": 7, "position": 4, "technical": 3, "volume": 2},
        },
        {
            "code": "600639.SH",
            "name": "浦东金桥",
            "score": 11,
            "rating": "★★★☆☆ 关注",
            "trade_bias": "watch",
            "risk_flags": ["ROE低于8%", "120日位置偏高"],
            "factor_scores": {"valuation": 6, "quality": -1, "position": 2, "technical": 3, "volume": 1},
        },
        {
            "code": "000000.SH",
            "name": "缺数据",
            "score": None,
            "rating": "★★☆☆☆ 观察",
            "trade_bias": "watch",
            "risk_flags": ["数据不足"],
            "factor_scores": {},
        },
    ]


def test_summarize_l2_score_distribution_counts_bucket_and_risks():
    summary = summarize_l2_score_distribution(_sample_rows())

    assert summary["total"] == 3
    assert summary["bucket_counts"] == {"<6": 0, "6-9": 0, "10-13": 1, "14-17": 0, "18+": 1, "missing": 1}
    assert summary["trade_bias_counts"] == {"right_side_candidate": 1, "watch": 2}
    assert summary["top_risk_flags"][0] == {"flag": "ROE低于8%", "count": 1}
    assert summary["factor_score_avg"]["valuation"] == 6.0
    assert summary["factor_score_avg"]["quality"] == 3.0
    assert summary["top_candidates"][0]["code"] == "601298.SH"
    assert summary["bottom_candidates"][0]["code"] == "600639.SH"


def test_build_l2_report_summary_from_candidate_score_map():
    summary = build_l2_report_summary({row["code"]: row for row in _sample_rows()})

    assert summary["total"] == 3
    assert summary["strong_count"] == 1
    assert summary["watch_count"] == 1
    assert summary["excluded_count"] == 0
    assert summary["missing_count"] == 1
    assert summary["right_side_count"] == 1
    assert summary["top_risk_flags"][0]["flag"] == "ROE低于8%"
    assert "18+" in summary["bucket_text"]


def test_build_l2_report_summary_extracts_near_strong_candidates():
    rows = _sample_rows()
    rows.append(
        {
            "code": "605305.SH",
            "name": "中际联合",
            "score": 15,
            "rating": "★★★★☆ 推荐",
            "trade_bias": "watch",
            "risk_flags": ["20日涨幅偏高", "非多头排列"],
            "factor_scores": {"valuation": 3, "quality": 4, "position": 4, "technical": 2, "volume": 2},
            "no_trade_reason": "20日涨幅偏高；非多头排列",
        }
    )

    summary = build_l2_report_summary({row["code"]: row for row in rows})

    assert summary["near_strong_count"] == 1
    candidate = summary["near_strong_candidates"][0]
    assert candidate["code"] == "605305.SH"
    assert candidate["score"] == 15
    assert candidate["gap_to_strong"] == 3
    assert candidate["blocking_reasons"] == ["20日涨幅偏高", "非多头排列"]


def test_build_l2_report_summary_classifies_near_strong_blockers_and_suggests_review_only():
    rows = _sample_rows()
    rows.extend([
        {
            "code": "605305.SH",
            "name": "中际联合",
            "score": 15,
            "rating": "★★★★☆ 推荐",
            "trade_bias": "watch",
            "risk_flags": ["20日涨幅偏高", "非多头排列"],
            "factor_scores": {},
        },
        {
            "code": "605116.SH",
            "name": "奥锐特",
            "score": 14,
            "rating": "★★★★☆ 推荐",
            "trade_bias": "watch",
            "risk_flags": ["成交比不足", "ROE低于8%"],
            "factor_scores": {},
        },
    ])

    summary = build_l2_report_summary({row["code"]: row for row in rows})

    assert summary["near_strong_blocker_categories"] == [
        {"category": "技术趋势未修复", "count": 1},
        {"category": "短期涨幅过高", "count": 1},
        {"category": "量能不足", "count": 1},
        {"category": "ROE/质量不足", "count": 1},
    ]
    assert summary["tuning_suggestion"]["mode"] == "review_only"
    assert "不自动放宽" in summary["tuning_suggestion"]["text"]
