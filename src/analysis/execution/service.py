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
DEFAULT_ENTRY_CONDITION = "等待 MACD 金叉、KDJ 低位金叉、布林带收口突破共同确认"
CONFIRMED_ENTRY_CONDITION = "右侧触发已确认：MACD金叉 + KDJ低位金叉 + 布林带收口突破 + 量能确认"


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


def _truthy_flag(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1", "confirmed", "pass", "passed"}:
        return True
    if text in {"false", "no", "n", "0", "pending", "missing", "none"}:
        return False
    return None


def _merged_timing_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    timing: Dict[str, Any] = {}
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        timing.update(metrics)
    nested = payload.get("l3_timing") or payload.get("entry_triggers") or payload.get("timing")
    if isinstance(nested, dict):
        timing.update(nested)
    return timing


def _contains_any(value: Any, tokens: tuple[str, ...]) -> Optional[bool]:
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return any(token.lower() in text for token in tokens)


def _trigger_status(value: Optional[bool]) -> str:
    if value is None:
        return "missing_data"
    return "confirmed" if value else "pending"


def _build_l3_entry_triggers(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    timing = _merged_timing_payload(payload)

    macd = _truthy_flag(timing.get("macd_golden_cross"))
    if macd is None:
        macd = _contains_any(timing.get("macd_status"), ("金叉", "多头"))

    kdj = _truthy_flag(timing.get("kdj_low_cross"))
    if kdj is None:
        kdj_text = timing.get("kdj_status") or timing.get("kdj_signal")
        positive_kdj = _contains_any(kdj_text, ("低位金叉", "低位拐头", "低位向上"))
        negative_kdj = _contains_any(kdj_text, ("钝化", "高位", "死叉", "未金叉"))
        if positive_kdj is True:
            kdj = True
        elif negative_kdj is True:
            kdj = False

    boll = _truthy_flag(timing.get("bollinger_squeeze_breakout"))
    if boll is None:
        boll_text = timing.get("bollinger_status") or timing.get("boll_status") or timing.get("bollinger_signal")
        positive_boll = _contains_any(boll_text, ("收口突破", "缩口突破", "突破上轨", "放量突破"))
        negative_boll = _contains_any(boll_text, ("未突破", "收口未突破", "走平"))
        if positive_boll is True and negative_boll is not True:
            boll = True
        elif negative_boll is True:
            boll = False

    volume = _truthy_flag(timing.get("volume_confirmed"))
    if volume is None:
        ratio = _safe_float(timing.get("volume_ratio_20_120"))
        if ratio is not None:
            volume = ratio >= 1.05

    return {
        "macd_golden_cross": {
            "label": "MACD金叉",
            "status": _trigger_status(macd),
            "value": timing.get("macd_golden_cross", timing.get("macd_status")),
        },
        "kdj_low_cross": {
            "label": "KDJ低位金叉",
            "status": _trigger_status(kdj),
            "value": timing.get("kdj_low_cross", timing.get("kdj_status") or timing.get("kdj_signal")),
        },
        "bollinger_squeeze_breakout": {
            "label": "布林带收口突破",
            "status": _trigger_status(boll),
            "value": timing.get(
                "bollinger_squeeze_breakout",
                timing.get("bollinger_status") or timing.get("boll_status") or timing.get("bollinger_signal"),
            ),
        },
        "volume_confirmed": {
            "label": "量能确认",
            "status": _trigger_status(volume),
            "value": timing.get("volume_confirmed", timing.get("volume_ratio_20_120")),
        },
    }


def _entry_trigger_status(entry_triggers: Dict[str, Dict[str, Any]]) -> str:
    statuses = [trigger.get("status") for trigger in entry_triggers.values()]
    if statuses and all(status == "confirmed" for status in statuses):
        return "confirmed"
    if statuses and all(status == "missing_data" for status in statuses):
        return "missing_data"
    return "pending"


def _pending_entry_condition(entry_triggers: Dict[str, Dict[str, Any]]) -> str:
    if entry_triggers and all(trigger.get("status") == "missing_data" for trigger in entry_triggers.values()):
        return DEFAULT_ENTRY_CONDITION
    pending_labels = [
        str(trigger.get("label"))
        for trigger in entry_triggers.values()
        if trigger.get("status") != "confirmed" and trigger.get("label")
    ]
    if not pending_labels:
        return CONFIRMED_ENTRY_CONDITION
    return "等待" + "、".join(pending_labels) + "共同确认"


def _normalize_code(value: Any) -> str:
    raw = str(value or "").strip()
    if "." in raw:
        raw = raw.split(".", 1)[0]
    return raw


def _resolve_lot_size(stock_code: Optional[str]) -> Optional[int]:
    raw = str(stock_code or "").strip().lower()
    if not raw:
        return A_SHARE_LOT_SIZE
    normalized = _normalize_code(raw)
    if raw.startswith(("hk", "0")) and (raw.startswith("hk") or ".hk" in raw):
        return None
    if normalized.isdigit() and len(normalized) == 6:
        return A_SHARE_LOT_SIZE
    return 1


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


def _floor_to_lot_shares(value: Optional[float], price: Optional[float], lot_size: Optional[int] = A_SHARE_LOT_SIZE) -> Optional[int]:
    if value is None or price is None or price <= 0 or lot_size is None or lot_size <= 0:
        return None
    raw_shares = int(float(value) // float(price))
    return (raw_shares // lot_size) * lot_size


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
    lot_size = _resolve_lot_size(stock_code)
    suggested_shares = _floor_to_lot_shares(cash_limited_value, current_price, lot_size)

    if suggested_shares is not None:
        if suggested_shares > 0:
            risk_notes.append(f"账户约束已按{lot_size}股一手取整，实际成交需再看盘中流动性与滑点。")
        else:
            risk_notes.append("账户约束后不足一手，不生成实际买入股数。")
    elif lot_size is None:
        risk_notes.append("暂未接入该市场最小交易单位，不生成实际买入股数。")

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
        "lot_size": lot_size,
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
    entry_triggers = _build_l3_entry_triggers(payload)
    entry_trigger_status = _entry_trigger_status(entry_triggers)
    if entry_trigger_status == "confirmed":
        entry_condition = CONFIRMED_ENTRY_CONDITION
    else:
        entry_condition = _pending_entry_condition(entry_triggers)
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
        "entry_condition": entry_condition,
        "entry_triggers": entry_triggers,
        "entry_trigger_status": entry_trigger_status,
        "initial_position_fraction": INITIAL_POSITION_FRACTION,
        "max_single_stock_weight": MAX_SINGLE_STOCK_WEIGHT,
        "hard_stop_loss_pct": HARD_STOP_LOSS_PCT,
        "time_stop_days": TIME_STOP_DAYS,
        "account_constraints": account_constraints,
        "risk_notes": risk_notes,
    }


def _is_final_buy_decision(result: Any) -> bool:
    decision_type = str(getattr(result, "decision_type", "") or "").strip().lower()
    if decision_type == "buy":
        return True
    advice = str(getattr(result, "operation_advice", "") or "").strip().lower()
    return advice in {"买入", "加仓", "buy", "add", "add_position", "strong buy", "strong_buy"}


def _has_hard_guardrail(result: Any) -> bool:
    report = getattr(result, "normalization_report", None)
    if not isinstance(report, dict):
        return False
    if report.get("max_severity") == "hard_guardrail":
        return True
    for record in report.get("applied_rules", []) or []:
        if isinstance(record, dict) and record.get("changed") and record.get("severity") == "hard_guardrail":
            return True
    return False


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
        if not _is_final_buy_decision(result) or _has_hard_guardrail(result):
            continue
        plan = build_execution_plan(
            payload,
            portfolio_snapshot=portfolio_snapshot,
            stock_code=code,
            current_price=getattr(result, "current_price", None),
        )
        if plan.get("eligible_for_l3"):
            plan_map[code] = plan
    return plan_map
