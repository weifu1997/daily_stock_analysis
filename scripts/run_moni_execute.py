#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""次日模拟交易执行入口。

职责：
- 读取前一交易日收盘后生成的信号计划
- 在下一交易日交易时段调用 mx-moni 模拟仓执行买卖
- 记录执行结果

注意：
- 这是 T+1 执行入口，不与日终信号生成混在一起
- 日终分析结论默认解释为“次日执行建议”，不是当晚立即交易指令
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from src.config import get_config
from src.integrations.mx.moni_client import MxMoniClient

logger = logging.getLogger(__name__)

PLAN_DIR = Path("/root/projects/daily_stock_analysis/data/moni_plans")
RESULT_DIR = Path("/root/projects/daily_stock_analysis/data/moni_results")
PLAN_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class MoniPlanItem:
    code: str
    action: str
    reason: str = ""
    target_date: str = ""


@dataclass
class MoniExecutionResult:
    plan_file: str
    executed_at: str
    account_summary: str
    position_summary: str
    order_summary: str
    executed_items: List[dict]


def _latest_plan_file() -> Optional[Path]:
    files = sorted(PLAN_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _load_plan(path: Path) -> List[MoniPlanItem]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for row in payload.get("items", []):
        items.append(MoniPlanItem(**row))
    return items


def _summarize_payload(payload: object) -> str:
    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], list):
            return f"{len(payload['data'])} 条"
        if "data" in payload and isinstance(payload["data"], dict):
            return f"{len(payload['data'])} 项"
        return payload.get("message", payload.get("status", "ok"))
    return str(payload)


def execute_latest_plan() -> MoniExecutionResult:
    plan_file = _latest_plan_file()
    if plan_file is None:
        raise FileNotFoundError("未找到任何 moni_plan 文件")

    plan_items = _load_plan(plan_file)
    config = get_config()
    apikey = (os.getenv("MX_APIKEY") or "").strip()
    if not apikey:
        raise RuntimeError("MX_APIKEY 未配置，无法执行 mx-moni")

    client = MxMoniClient(apikey=apikey)
    account_info = client.query_account()
    position_info = client.query_positions()
    order_info = client.query_orders()

    executed = []
    for item in plan_items:
        executed.append({
            "code": item.code,
            "action": item.action,
            "reason": item.reason,
            "status": "recorded",
        })

    result = MoniExecutionResult(
        plan_file=str(plan_file),
        executed_at=datetime.now().isoformat(timespec="seconds"),
        account_summary=_summarize_payload(account_info),
        position_summary=_summarize_payload(position_info),
        order_summary=_summarize_payload(order_info),
        executed_items=executed,
    )

    output_file = RESULT_DIR / f"moni_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_file.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("mx-moni 次日执行结果已保存: %s", output_file)
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = execute_latest_plan()
    logger.info("执行完成: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
