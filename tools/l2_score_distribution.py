#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.analysis.candidate_layers.distribution import build_l2_report_summary, summarize_l2_score_distribution
from src.services.candidate_scoring_service import CandidateScoringService


def _safe_json_loads(value: Any) -> Dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _extract_fundamental_context(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    enhanced = snapshot.get("enhanced_context") if isinstance(snapshot, dict) else None
    if not isinstance(enhanced, dict):
        return None
    for key in ("fundamental_context", "fundamental", "fundamentals"):
        value = enhanced.get(key)
        if isinstance(value, dict):
            return value
    return None


def _load_analysis_rows(conn: sqlite3.Connection, limit: int) -> List[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT id, code, name, sentiment_score, operation_advice, context_snapshot, created_at
        FROM analysis_history
        WHERE code IS NOT NULL
        ORDER BY id DESC
        LIMIT ?
    """
    return list(conn.execute(sql, (limit,)).fetchall())


def _load_daily_df(conn: sqlite3.Connection, code: str, before_created_at: str, days: int = 180) -> pd.DataFrame:
    # Prefer rows up to analysis date if created_at is parseable; otherwise latest known rows.
    date_limit = None
    try:
        date_limit = datetime.fromisoformat(str(before_created_at)).date().isoformat()
    except Exception:
        pass
    if date_limit:
        sql = """
            SELECT date, open, high, low, close, volume, amount
            FROM stock_daily
            WHERE code = ? AND date <= ?
            ORDER BY date DESC
            LIMIT ?
        """
        rows = conn.execute(sql, (code, date_limit, days)).fetchall()
    else:
        sql = """
            SELECT date, open, high, low, close, volume, amount
            FROM stock_daily
            WHERE code = ?
            ORDER BY date DESC
            LIMIT ?
        """
        rows = conn.execute(sql, (code, days)).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(row) for row in rows])
    return df.sort_values("date").reset_index(drop=True)


def build_l2_distribution_from_db(db_path: str, limit: int = 120, dedupe_by_code: bool = True) -> Dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    service = CandidateScoringService()
    scored: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    seen_codes: set[str] = set()

    for row in _load_analysis_rows(conn, limit):
        code = row["code"]
        if dedupe_by_code and code in seen_codes:
            continue
        seen_codes.add(code)
        snapshot = _safe_json_loads(row["context_snapshot"])
        fundamental_context = _extract_fundamental_context(snapshot)
        source_meta = {}
        if isinstance(snapshot, dict):
            source_map = snapshot.get("candidate_source_map") or snapshot.get("l1_candidate_source_map") or {}
            if isinstance(source_map, dict):
                source_meta = source_map.get(code) or source_map.get(str(code)) or {}
        daily_df = _load_daily_df(conn, code, row["created_at"] or "")
        try:
            score = service.score_candidate(
                code=code,
                name=row["name"] or "",
                daily_df=daily_df,
                trend_result=None,
                fundamental_context=fundamental_context,
            ).to_dict()
            score.update(
                {
                    "analysis_id": row["id"],
                    "sentiment_score": row["sentiment_score"],
                    "operation_advice": row["operation_advice"],
                    "created_at": row["created_at"],
                    "candidate_source": source_meta.get("candidate_source", "") if isinstance(source_meta, dict) else "",
                    "source_query": source_meta.get("source_query", "") if isinstance(source_meta, dict) else "",
                    "source_profile": source_meta.get("source_profile", "") if isinstance(source_meta, dict) else "",
                    "source_rank": source_meta.get("source_rank", "") if isinstance(source_meta, dict) else "",
                    "pool_reason": source_meta.get("pool_reason", "") if isinstance(source_meta, dict) else "",
                    "forced_by_portfolio": bool(source_meta.get("forced_by_portfolio", False)) if isinstance(source_meta, dict) else False,
                    "source_fallback_used": bool(source_meta.get("fallback_used", False)) if isinstance(source_meta, dict) else False,
                }
            )
            scored.append(score)
        except Exception as exc:  # fail-open for analysis tooling
            skipped.append({"id": row["id"], "code": code, "error": str(exc)})

    summary = summarize_l2_score_distribution(scored)
    summary["report_summary"] = build_l2_report_summary({str(row.get("code")): row for row in scored})
    summary["source"] = {
        "db_path": db_path,
        "requested_limit": limit,
        "dedupe_by_code": dedupe_by_code,
        "scored": len(scored),
        "skipped": len(skipped),
    }
    summary["rows"] = scored
    summary["skipped_samples"] = skipped[:10]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L2 candidate score distribution from local daily_stock_analysis DB.")
    parser.add_argument("--db", default="data/stock_analysis.db")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--output", default="reports/l2_score_distribution_latest.json")
    parser.add_argument("--no-dedupe", action="store_true", help="Keep repeated historical analyses for the same stock code.")
    args = parser.parse_args()

    result = build_l2_distribution_from_db(args.db, args.limit, dedupe_by_code=not args.no_dedupe)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
