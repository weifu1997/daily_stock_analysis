# -*- coding: utf-8 -*-
"""Minimal L3 execution plan builder.

L3 is deliberately conservative: it only turns L2-approved candidates into a
structured watch-for-entry plan. It does not auto-buy and does not mutate
``operation_advice``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

L3_MIN_SCORE = 18
INITIAL_POSITION_FRACTION = 0.33
MAX_SINGLE_STOCK_WEIGHT = 0.10
HARD_STOP_LOSS_PCT = -8
TIME_STOP_DAYS = 30
DEFAULT_ENTRY_CONDITION = "等待放量突破后回踩不破"


def _safe_score(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        score = float(value)
        if score != score:  # NaN
            return None
        return score
    except Exception:
        return None


def build_execution_plan(candidate_layer_score: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a minimal L3 execution plan from one L2 score payload."""
    payload = candidate_layer_score if isinstance(candidate_layer_score, dict) else {}
    score = _safe_score(payload.get("score"))
    trade_bias = str(payload.get("trade_bias") or "").strip().lower()

    base: Dict[str, Any] = {
        "eligible_for_l3": False,
        "action": "no_trade",
        "reason_code": "l2_missing_score" if score is None else "l2_not_eligible_for_l3",
        "entry_condition": None,
        "initial_position_fraction": None,
        "max_single_stock_weight": None,
        "hard_stop_loss_pct": None,
        "time_stop_days": None,
        "risk_notes": [],
    }
    if score is None:
        return base
    if score < L3_MIN_SCORE:
        return base
    if trade_bias != "right_side_candidate":
        base["reason_code"] = "l2_not_right_side_candidate"
        return base

    risk_notes = list(payload.get("risk_flags") or [])
    risk_notes.append("L3仅生成观察入场计划，不自动买入；需右侧触发后再按仓位执行。")
    return {
        "eligible_for_l3": True,
        "action": "watch_for_entry",
        "reason_code": "l3_watch_entry_plan",
        "entry_condition": payload.get("entry_hint") or DEFAULT_ENTRY_CONDITION,
        "initial_position_fraction": INITIAL_POSITION_FRACTION,
        "max_single_stock_weight": MAX_SINGLE_STOCK_WEIGHT,
        "hard_stop_loss_pct": HARD_STOP_LOSS_PCT,
        "time_stop_days": TIME_STOP_DAYS,
        "risk_notes": risk_notes,
    }


def build_execution_plan_map(results: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """Build per-stock L3 execution plan map for scored analysis results."""
    plan_map: Dict[str, Dict[str, Any]] = {}
    for result in results or []:
        payload = getattr(result, "candidate_layer_score", None)
        if not isinstance(payload, dict):
            continue
        code = getattr(result, "code", None)
        if not code:
            continue
        plan_map[code] = build_execution_plan(payload)
    return plan_map
