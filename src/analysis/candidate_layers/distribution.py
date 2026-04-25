from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

_BUCKET_ORDER = ["<6", "6-9", "10-13", "14-17", "18+", "missing"]


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def assign_l2_score_bucket(score: Any) -> str:
    value = _safe_float(score)
    if value is None:
        return "missing"
    if value < 6:
        return "<6"
    if value < 10:
        return "6-9"
    if value < 14:
        return "10-13"
    if value < 18:
        return "14-17"
    return "18+"


def _rows_from_score_map(candidate_score_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for code, payload in (candidate_score_map or {}).items():
        if not isinstance(payload, dict):
            continue
        row = dict(payload)
        row.setdefault("code", code)
        rows.append(row)
    return rows


def summarize_l2_score_distribution(rows: Iterable[Dict[str, Any]], top_n: int = 10) -> Dict[str, Any]:
    materialized = [row for row in rows if isinstance(row, dict)]
    bucket_counts = {bucket: 0 for bucket in _BUCKET_ORDER}
    trade_bias_counter: Counter[str] = Counter()
    risk_counter: Counter[str] = Counter()
    factor_totals: Dict[str, float] = defaultdict(float)
    factor_counts: Counter[str] = Counter()

    scored_rows: List[Dict[str, Any]] = []
    for row in materialized:
        bucket_counts[assign_l2_score_bucket(row.get("score"))] += 1
        trade_bias_counter[str(row.get("trade_bias") or "unknown")] += 1
        for flag in row.get("risk_flags") or []:
            if flag:
                risk_counter[str(flag)] += 1
        factor_scores = row.get("factor_scores") or {}
        if isinstance(factor_scores, dict):
            for key, value in factor_scores.items():
                numeric = _safe_float(value)
                if numeric is None:
                    continue
                factor_totals[str(key)] += numeric
                factor_counts[str(key)] += 1
        numeric_score = _safe_float(row.get("score"))
        if numeric_score is not None:
            scored_rows.append(row)

    factor_avg = {
        key: round(factor_totals[key] / factor_counts[key], 2)
        for key in sorted(factor_totals)
        if factor_counts[key]
    }
    ranked = sorted(scored_rows, key=lambda item: _safe_float(item.get("score")) or 0, reverse=True)

    def _candidate_view(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "code": row.get("code"),
            "name": row.get("name"),
            "score": row.get("score"),
            "rating": row.get("rating"),
            "trade_bias": row.get("trade_bias"),
            "risk_flags": row.get("risk_flags") or [],
        }

    return {
        "total": len(materialized),
        "bucket_counts": bucket_counts,
        "trade_bias_counts": dict(sorted(trade_bias_counter.items())),
        "top_risk_flags": [
            {"flag": flag, "count": count}
            for flag, count in risk_counter.most_common(top_n)
        ],
        "factor_score_avg": factor_avg,
        "top_candidates": [_candidate_view(row) for row in ranked[:top_n]],
        "bottom_candidates": [_candidate_view(row) for row in list(reversed(ranked[-top_n:]))],
    }


def _blocking_reasons(row: Dict[str, Any]) -> List[str]:
    reasons = row.get("risk_flags") or []
    if not reasons and row.get("no_trade_reason"):
        reasons = [part.strip() for part in str(row.get("no_trade_reason")).split("；") if part.strip()]
    return [str(reason) for reason in reasons[:3] if reason]


def _classify_blocker(reason: str) -> str:
    text = str(reason)
    if "ROE" in text or "质量" in text or "分红" in text:
        return "ROE/质量不足"
    if "成交" in text or "量能" in text:
        return "量能不足"
    if "涨幅偏高" in text or "位置偏高" in text:
        return "短期涨幅过高"
    if "多头" in text or "MA" in text or "趋势" in text or "位置偏低" in text:
        return "技术趋势未修复"
    if "估值" in text or "PB" in text or "PE" in text:
        return "估值不够友好"
    return "其他"


def _near_strong_blocker_categories(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Counter[str] = Counter()
    first_seen_order: List[str] = []
    for candidate in candidates:
        seen_for_candidate: set[str] = set()
        for reason in candidate.get("blocking_reasons") or []:
            category = _classify_blocker(reason)
            if category in seen_for_candidate:
                continue
            seen_for_candidate.add(category)
            if category not in counts:
                first_seen_order.append(category)
            counts[category] += 1
    priority = ["技术趋势未修复", "短期涨幅过高", "量能不足", "ROE/质量不足", "估值不够友好", "其他"]
    return [
        {"category": category, "count": counts[category]}
        for category in sorted(
            first_seen_order,
            key=lambda item: (-counts[item], priority.index(item) if item in priority else len(priority)),
        )
    ]


def _tuning_suggestion(categories: List[Dict[str, Any]]) -> Dict[str, str]:
    if not categories:
        text = "近强候选暂无明确共性卡点；继续旁路观察，不自动调整阈值。"
    else:
        top = categories[0]["category"]
        text = f"近强候选主要卡在{top}；只读观察，不自动放宽阈值，先看后续右侧确认。"
    return {"mode": "review_only", "text": text}


def _near_strong_candidates(rows: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        score = _safe_float(row.get("score"))
        if score is None or not (14 <= score < 18):
            continue
        candidates.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "score": int(round(score)),
            "rating": row.get("rating"),
            "gap_to_strong": int(round(18 - score)),
            "blocking_reasons": _blocking_reasons(row),
        })
    return sorted(candidates, key=lambda item: item.get("score") or 0, reverse=True)[:limit]


def build_l2_report_summary(candidate_score_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    rows = _rows_from_score_map(candidate_score_map)
    summary = summarize_l2_score_distribution(rows, top_n=5)
    bucket_counts = summary.get("bucket_counts") or {}
    trade_bias_counts = summary.get("trade_bias_counts") or {}
    near_strong = _near_strong_candidates(rows)
    blocker_categories = _near_strong_blocker_categories(near_strong)
    bucket_text = " / ".join(
        f"{bucket}:{bucket_counts.get(bucket, 0)}"
        for bucket in _BUCKET_ORDER
        if bucket_counts.get(bucket, 0)
    ) or "无"
    return {
        "total": summary.get("total", 0),
        "strong_count": bucket_counts.get("18+", 0),
        "watch_count": bucket_counts.get("10-13", 0) + bucket_counts.get("14-17", 0),
        "excluded_count": bucket_counts.get("<6", 0),
        "missing_count": bucket_counts.get("missing", 0),
        "right_side_count": trade_bias_counts.get("right_side_candidate", 0),
        "near_strong_count": len(near_strong),
        "near_strong_candidates": near_strong,
        "near_strong_blocker_categories": blocker_categories,
        "tuning_suggestion": _tuning_suggestion(blocker_categories),
        "bucket_counts": bucket_counts,
        "bucket_text": bucket_text,
        "top_risk_flags": summary.get("top_risk_flags", []),
        "factor_score_avg": summary.get("factor_score_avg", {}),
    }
