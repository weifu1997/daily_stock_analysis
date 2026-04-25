import csv
import json
import sqlite3
from pathlib import Path

from tools.l2_observation_ledger import build_observation_from_db, build_observation_rows, write_observation_outputs


def _score_payload(**overrides):
    payload = {
        "code": "605305",
        "name": "中际联合",
        "score": 15,
        "rating": "★★★★☆ 推荐",
        "trade_bias": "watch",
        "risk_flags": ["20日涨幅偏高"],
        "operation_advice": "观望",
        "sentiment_score": 63,
        "metrics": {"close": 41.2},
        "candidate_source": "mx_preselect",
        "source_query": "A股 正常交易 低估值 高ROE",
        "source_profile": "env_query",
        "source_rank": 2,
        "pool_reason": "mx_xuangu_selected",
        "forced_by_portfolio": False,
        "source_fallback_used": False,
    }
    payload.update(overrides)
    return payload


def test_build_observation_rows_marks_near_strong_and_blocker_categories():
    rows = build_observation_rows([_score_payload()], as_of_date="2026-04-24")

    assert rows == [
        {
            "date": "2026-04-24",
            "code": "605305",
            "name": "中际联合",
            "score": 15,
            "rating": "★★★★☆ 推荐",
            "trade_bias": "watch",
            "bucket": "14-17",
            "is_near_strong": True,
            "gap_to_strong": 3,
            "blocking_reasons": "20日涨幅偏高",
            "blocker_categories": "短期涨幅过高",
            "right_side_candidate": False,
            "operation_advice": "观望",
            "sentiment_score": 63,
            "close": 41.2,
            "candidate_source": "mx_preselect",
            "source_query": "A股 正常交易 低估值 高ROE",
            "source_profile": "env_query",
            "source_rank": 2,
            "pool_reason": "mx_xuangu_selected",
            "forced_by_portfolio": False,
            "source_fallback_used": False,
        }
    ]


def test_write_observation_outputs_writes_daily_json_and_upserts_csv(tmp_path: Path):
    output_dir = tmp_path / "l2_observation"
    ledger_path = output_dir / "l2_observation_ledger.csv"
    rows = build_observation_rows([_score_payload()], as_of_date="2026-04-24")

    result = write_observation_outputs(rows, output_dir=output_dir, ledger_path=ledger_path, as_of_date="2026-04-24")
    result_again = write_observation_outputs(rows, output_dir=output_dir, ledger_path=ledger_path, as_of_date="2026-04-24")

    daily_path = output_dir / "2026-04-24.json"
    assert result["daily_path"] == str(daily_path)
    assert result_again["ledger_rows"] == 1
    daily = json.loads(daily_path.read_text(encoding="utf-8"))
    assert daily["date"] == "2026-04-24"
    assert daily["rows"][0]["code"] == "605305"

    with ledger_path.open(encoding="utf-8", newline="") as fh:
        csv_rows = list(csv.DictReader(fh))
    assert len(csv_rows) == 1
    assert csv_rows[0]["date"] == "2026-04-24"
    assert csv_rows[0]["code"] == "605305"
    assert csv_rows[0]["is_near_strong"] == "True"
    assert csv_rows[0]["candidate_source"] == "mx_preselect"
    assert csv_rows[0]["source_rank"] == "2"


def test_build_observation_from_db_reads_candidate_source_from_context_snapshot(tmp_path: Path):
    db_path = tmp_path / "stock_analysis.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE analysis_history (
            id INTEGER PRIMARY KEY,
            code TEXT,
            name TEXT,
            sentiment_score INTEGER,
            operation_advice TEXT,
            context_snapshot TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE stock_daily (
            code TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL
        )
        """
    )
    snapshot = {
        "enhanced_context": {},
        "candidate_source_map": {
            "605305": {
                "candidate_source": "mx_preselect",
                "source_query": "A股 正常交易 低估值 高ROE",
                "source_profile": "env_query",
                "source_rank": 3,
                "pool_reason": "mx_xuangu_selected",
                "forced_by_portfolio": False,
                "fallback_used": False,
            }
        },
    }
    conn.execute(
        "INSERT INTO analysis_history VALUES (1, '605305', '中际联合', 63, '观望', ?, '2026-04-24 18:00:00')",
        (json.dumps(snapshot, ensure_ascii=False),),
    )
    for idx in range(140):
        price = 10 + idx * 0.1
        conn.execute(
            "INSERT INTO stock_daily VALUES ('605305', ?, ?, ?, ?, ?, ?, ?)",
            (f"2026-01-{(idx % 28) + 1:02d}", price, price + 1, price - 1, price, 1000 + idx, 100000 + idx),
        )
    conn.commit()
    conn.close()

    built = build_observation_from_db(str(db_path), limit=10, as_of_date="2026-04-24")

    assert built["rows"][0]["candidate_source"] == "mx_preselect"
    assert built["rows"][0]["source_query"] == "A股 正常交易 低估值 高ROE"
    assert built["rows"][0]["source_rank"] == 3
    assert built["rows"][0]["pool_reason"] == "mx_xuangu_selected"
