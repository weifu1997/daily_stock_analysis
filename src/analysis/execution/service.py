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
A_SHARE_LOT_SIZE = 100
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


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        if number != number:  # NaN
            return None
        return number
    except Exception:
        return None


def _round_money(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 2)


def _normalize_code(value: Any) -> str:
    raw = str(value or "").strip()
    if "." in raw:
        raw = raw.split(".", 1)[0]
    return raw


def _find_position(portfolio_snapshot: Optional[Dict[str, Any]], stock_code: Optional[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(portfolio_snapshot, dict) or not stock_code:
        return None
    target = _normalize_code(stock_code)
    for account in portfolio_snapshot.get("accounts", []) or []:
        if not isinstance(account, dict):
            continue
        for position in account.get("positions", []) or []:
            if not isinstance(position, dict):
                continue
            raw_code = position.get("symbol") or position.get("code")
            if _normalize_code(raw_code) == target:
                return position
    return None


def _floor_to_lot_shares(value: Optional[float], price: Optional[float]) -> Optional[int]:
    if value is None or price is None or price <= 0:
        return None
    raw_shares = int(float(value) // float(price))
    return (raw_shares // A_SHARE_LOT_SIZE) * A_SHARE_LOT_SIZE


def _build_account_constraints(
    *,
    portfolio_snapshot: Optional[Dict[str, Any]],
    stock_code: Optional[str],
    current_price: Optional[float],
    risk_notes: list[str],
) -> Optional[Dict[str, Any]]:
    if not isinstance(portfolio_snapshot, dict):
        return None

    total_cash = _safe_float(portfolio_snapshot.get("total_cash"))
    total_equity = _safe_float(portfolio_snapshot.get("total_equity"))
    position = _find_position(portfolio_snapshot, stock_code)
    current_position_value = _safe_float((position or {}).get("market_value_base")) or 0.0
    has_position = bool(position)

    max_position_value = total_equity * MAX_SINGLE_STOCK_WEIGHT if total_equity is not None else None
    target_entry_value = max_position_value * INITIAL_POSITION_FRACTION if max_position_value is not None else None
    max_additional_value = None
    if max_position_value is not None:
        max_additional_value = max(0.0, max_position_value - current_position_value)

    candidates = [value for value in (target_entry_value, max_additional_value, total_cash) if value is not None]
    cash_limited_value = min(candidates) if candidates else None
    suggested_shares = _floor_to_lot_shares(cash_limited_value, current_price)

    if suggested_shares is not None:
        if suggested_shares > 0:
            risk_notes.append("账户约束已按100股一手取整，实际成交需再看盘中流动性与滑点。")
        else:
            risk_notes.append("账户约束后不足一手，不生成实际买入股数。")

    return {
        "has_position": has_position,
        "available_cash": _round_money(total_cash),
        "total_equity": _round_money(total_equity),
        "current_position_value": _round_money(current_position_value),
        "max_position_value": _round_money(max_position_value),
        "target_entry_value": _round_money(target_entry_value),
        "max_additional_value": _round_money(max_additional_value),
        "cash_limited_value": _round_money(cash_limited_value),
        "suggested_shares": suggested_shares,
        "lot_size": A_SHARE_LOT_SIZE,
        "price_used": _round_money(current_price),
    }


def build_execution_plan(
    candidate_layer_score: Optional[Dict[str, Any]],
    *,
    portfolio_snapshot: Optional[Dict[str, Any]] = None,
    stock_code: Optional[str] = None,
    current_price: Optional[float] = None,
) -> Dict[str, Any]:
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
        "account_constraints": None,
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
    account_constraints = _build_account_constraints(
        portfolio_snapshot=portfolio_snapshot,
        stock_code=stock_code,
        current_price=_safe_float(current_price),
        risk_notes=risk_notes,
    )
    return {
        "eligible_for_l3": True,
        "action": "watch_for_entry",
        "reason_code": "l3_watch_entry_plan",
        "entry_condition": payload.get("entry_hint") or DEFAULT_ENTRY_CONDITION,
        "initial_position_fraction": INITIAL_POSITION_FRACTION,
        "max_single_stock_weight": MAX_SINGLE_STOCK_WEIGHT,
        "hard_stop_loss_pct": HARD_STOP_LOSS_PCT,
        "time_stop_days": TIME_STOP_DAYS,
        "account_constraints": account_constraints,
        "risk_notes": risk_notes,
    }


def build_execution_plan_map(
    results: Iterable[Any],
    *,
    portfolio_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build per-stock L3 execution plan map for scored analysis results."""
    plan_map: Dict[str, Dict[str, Any]] = {}
    for result in results or []:
        payload = getattr(result, "candidate_layer_score", None)
        if not isinstance(payload, dict):
            continue
        code = getattr(result, "code", None)
        if not code:
            continue
        plan_map[code] = build_execution_plan(
            payload,
            portfolio_snapshot=portfolio_snapshot,
            stock_code=code,
            current_price=getattr(result, "current_price", None),
        )
    return plan_map
