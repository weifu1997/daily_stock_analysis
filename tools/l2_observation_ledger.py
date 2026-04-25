#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.candidate_layers.distribution import assign_l2_score_bucket, build_l2_report_summary
from tools.l2_score_distribution import build_l2_distribution_from_db

FIELDNAMES = [
    "date",
    "code",
    "name",
    "score",
    "rating",
    "trade_bias",
    "bucket",
    "is_near_strong",
    "gap_to_strong",
    "blocking_reasons",
    "blocker_categories",
    "right_side_candidate",
    "operation_advice",
    "sentiment_score",
    "close",
    "candidate_source",
    "source_query",
    "source_profile",
    "source_rank",
    "pool_reason",
    "forced_by_portfolio",
    "source_fallback_used",
]


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _risk_flags(payload: Dict[str, Any]) -> List[str]:
    flags = payload.get("risk_flags") or []
    if isinstance(flags, str):
        return [part.strip() for part in flags.replace("；", ";").split(";") if part.strip()]
    if isinstance(flags, list):
        return [str(item) for item in flags if item]
    return []


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


def _unique_categories(reasons: Iterable[str]) -> List[str]:
    categories: List[str] = []
    for reason in reasons:
        category = _classify_blocker(reason)
        if category not in categories:
            categories.append(category)
    return categories


def build_observation_rows(scored_rows: List[Dict[str, Any]], as_of_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Flatten L2 scored rows into the stable daily observation ledger schema."""
    obs_date = as_of_date or date.today().isoformat()
    rows: List[Dict[str, Any]] = []
    for payload in scored_rows:
        score = _safe_float(payload.get("score"))
        bucket = assign_l2_score_bucket(score)
        is_near_strong = score is not None and 14 <= score < 18
        gap_to_strong = int(18 - score) if is_near_strong else ""
        reasons = _risk_flags(payload)[:5]
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        trade_bias = payload.get("trade_bias") or ""
        rows.append(
            {
                "date": obs_date,
                "code": payload.get("code") or "",
                "name": payload.get("name") or "",
                "score": int(score) if score is not None and score.is_integer() else score,
                "rating": payload.get("rating") or "",
                "trade_bias": trade_bias,
                "bucket": bucket,
                "is_near_strong": bool(is_near_strong),
                "gap_to_strong": gap_to_strong,
                "blocking_reasons": "；".join(reasons),
                "blocker_categories": "；".join(_unique_categories(reasons)),
                "right_side_candidate": trade_bias == "right_side_candidate",
                "operation_advice": payload.get("operation_advice") or "",
                "sentiment_score": payload.get("sentiment_score") if payload.get("sentiment_score") is not None else "",
                "close": metrics.get("close") if metrics.get("close") is not None else payload.get("close", ""),
                "candidate_source": payload.get("candidate_source") or "",
                "source_query": payload.get("source_query") or "",
                "source_profile": payload.get("source_profile") or "",
                "source_rank": payload.get("source_rank") if payload.get("source_rank") is not None else "",
                "pool_reason": payload.get("pool_reason") or "",
                "forced_by_portfolio": bool(payload.get("forced_by_portfolio", False)),
                "source_fallback_used": bool(payload.get("source_fallback_used", payload.get("fallback_used", False))),
            }
        )
    return rows


def _read_existing_ledger(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _normalize_for_csv(row: Dict[str, Any]) -> Dict[str, Any]:
    return {field: row.get(field, "") for field in FIELDNAMES}


def write_observation_outputs(
    rows: List[Dict[str, Any]],
    output_dir: Path | str = Path("reports/l2_observation"),
    ledger_path: Path | str = Path("reports/l2_observation/l2_observation_ledger.csv"),
    as_of_date: Optional[str] = None,
) -> Dict[str, Any]:
    obs_date = as_of_date or (rows[0]["date"] if rows else date.today().isoformat())
    output_dir = Path(output_dir)
    ledger_path = Path(ledger_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    for row in rows:
        summary_row = dict(row)
        if isinstance(summary_row.get("blocking_reasons"), str):
            summary_row["risk_flags"] = [
                part.strip()
                for part in summary_row["blocking_reasons"].replace("；", ";").split(";")
                if part.strip()
            ]
        summary_rows.append(summary_row)
    report_summary = build_l2_report_summary({str(row.get("code")): row for row in summary_rows})
    daily_payload = {"date": obs_date, "count": len(rows), "summary": report_summary, "rows": rows}
    daily_path = output_dir / f"{obs_date}.json"
    daily_path.write_text(json.dumps(daily_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    existing = _read_existing_ledger(ledger_path)
    merged: Dict[tuple[str, str], Dict[str, Any]] = {
        (str(row.get("date", "")), str(row.get("code", ""))): row for row in existing
    }
    for row in rows:
        merged[(str(row.get("date", "")), str(row.get("code", "")))] = _normalize_for_csv(row)
    ordered = [merged[key] for key in sorted(merged.keys())]

    with ledger_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(_normalize_for_csv(row) for row in ordered)

    return {"daily_path": str(daily_path), "ledger_path": str(ledger_path), "daily_rows": len(rows), "ledger_rows": len(ordered)}


def _latest_analysis_date(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(date(created_at)) FROM analysis_history WHERE code IS NOT NULL").fetchone()
        return row[0] or date.today().isoformat()
    finally:
        conn.close()


def build_observation_from_db(db_path: str, limit: int = 120, as_of_date: Optional[str] = None, dedupe_by_code: bool = True) -> Dict[str, Any]:
    distribution = build_l2_distribution_from_db(db_path, limit=limit, dedupe_by_code=dedupe_by_code)
    rows = build_observation_rows(distribution.get("rows", []), as_of_date=as_of_date or _latest_analysis_date(db_path))
    return {"rows": rows, "distribution": distribution}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build daily L2 observation ledger from local daily_stock_analysis DB.")
    parser.add_argument("--db", default="data/stock_analysis.db")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--date", default=None, help="Observation date, defaults to latest analysis_history created_at date.")
    parser.add_argument("--output-dir", default="reports/l2_observation")
    parser.add_argument("--ledger", default="reports/l2_observation/l2_observation_ledger.csv")
    parser.add_argument("--no-dedupe", action="store_true", help="Keep repeated historical analyses for the same stock code.")
    args = parser.parse_args()

    built = build_observation_from_db(args.db, limit=args.limit, as_of_date=args.date, dedupe_by_code=not args.no_dedupe)
    result = write_observation_outputs(
        built["rows"],
        output_dir=Path(args.output_dir),
        ledger_path=Path(args.ledger),
        as_of_date=args.date or (built["rows"][0]["date"] if built["rows"] else None),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
