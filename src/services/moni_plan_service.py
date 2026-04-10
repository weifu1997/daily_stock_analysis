# -*- coding: utf-8 -*-
"""日终信号落盘与次日执行计划服务。

口径约束：
- 交易日 18:00 左右生成的分析结果，默认仅用于“次一交易日”执行
- 当日分析信号不得解释为当晚立即交易指令
- moni_plan 是 T 日收盘后生成、供 T+1 交易时段执行的计划文件
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from src.analyzer import AnalysisResult
from src.core.trading_calendar import get_market_now, is_market_open

PLAN_DIR = Path('/root/projects/daily_stock_analysis/data/moni_plans')
PLAN_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class MoniPlanItem:
    code: str
    name: str
    action: str
    reason: str
    target_date: str
    sentiment_score: int
    trend_prediction: str
    operation_advice: str
    current_price: Optional[float] = None


def _next_cn_trading_date(from_time: Optional[datetime] = None) -> str:
    """返回下一 A 股交易日日期字符串（YYYY-MM-DD）。"""
    current = get_market_now('cn', current_time=from_time)
    candidate = current.date() + timedelta(days=1)
    while not is_market_open('cn', candidate):
        candidate += timedelta(days=1)
    return candidate.isoformat()


def build_items(results: List[AnalysisResult], target_date: Optional[str] = None) -> List[MoniPlanItem]:
    """将日终分析结果转换为次一交易日执行计划。"""
    target_date = target_date or _next_cn_trading_date()
    items: List[MoniPlanItem] = []
    for r in results:
        advice = getattr(r, 'operation_advice', '') or '持有'
        action = 'HOLD'
        if advice in {'买入', '加仓'}:
            action = 'BUY'
        elif advice in {'卖出', '减仓'}:
            action = 'SELL'
        items.append(MoniPlanItem(
            code=r.code,
            name=getattr(r, 'name', ''),
            action=action,
            reason=getattr(r, 'analysis_summary', '')[:200],
            target_date=target_date,
            sentiment_score=int(getattr(r, 'sentiment_score', 50) or 50),
            trend_prediction=str(getattr(r, 'trend_prediction', '震荡')),
            operation_advice=str(advice),
            current_price=(float(getattr(r, 'current_price', 0.0) or 0.0) if getattr(r, 'current_price', None) is not None else None),
        ))
    return items


def save_plan(results: List[AnalysisResult], report_date: Optional[str] = None) -> Path:
    execution_date = _next_cn_trading_date()
    items = build_items(results, target_date=execution_date)
    payload = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'report_date': report_date or datetime.now().strftime('%Y-%m-%d'),
        'target_date': execution_date,
        'execution_policy': 'T日收盘后生成，T+1交易时段执行',
        'items': [asdict(item) for item in items],
    }
    out = PLAN_DIR / f"moni_plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return out
