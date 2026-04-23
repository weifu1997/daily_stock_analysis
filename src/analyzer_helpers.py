# -*- coding: utf-8 -*-
"""Shared helper functions extracted from src.analyzer.

Kept as a separate module so src/analyzer.py stays focused on the main
AnalysisResult / GeminiAnalyzer implementation while tests continue to import
these helpers from src.analyzer via re-exports.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.data.stock_mapping import STOCK_NAME_MAP
from src.report_language import get_placeholder_text, localize_chip_health

logger = logging.getLogger(__name__)


def _is_gpt5_family_model(model: str) -> bool:
    """Return True for gpt-5 family models that require temperature=1."""
    normalized = (model or "").strip().lower()
    if not normalized:
        return False
    short = normalized.rsplit("/", 1)[-1]
    return short.startswith("gpt-5")


class _LiteLLMStreamError(RuntimeError):
    """Internal error wrapper that records whether any text was streamed."""

    def __init__(self, message: str, *, partial_received: bool = False):
        super().__init__(message)
        self.partial_received = partial_received


def _build_llm_response_preview(response_text: str, limit: int = 300) -> str:
    """Build a log-safe preview that prefers parseable JSON over scratchpad preambles."""
    text = (response_text or "").strip()
    if not text:
        return ""

    def _parse_json_candidate(candidate: str) -> Optional[str]:
        candidate = (candidate or "").strip()
        if not candidate:
            return None
        try:
            parsed = json.loads(candidate)
            return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            return None

    json_fence_match = re.search(r"```json\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if json_fence_match:
        parsed_candidate = _parse_json_candidate(json_fence_match.group(1))
        if parsed_candidate:
            text = parsed_candidate
        else:
            text = re.sub(r"```.*?```", "", text, flags=re.DOTALL).strip()
    else:
        json_start = min(
            [idx for idx in (text.find("{"), text.find("[")) if idx >= 0],
            default=-1,
        )
        if json_start >= 0:
            parsed_candidate = _parse_json_candidate(text[json_start:])
            if parsed_candidate:
                text = parsed_candidate
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL).strip()

    if not text.startswith(("{", "[")):
        first_cjk_match = re.search(r"[\u4e00-\u9fff]", text)
        if first_cjk_match and first_cjk_match.start() > 0:
            prefix = text[: first_cjk_match.start()]
            if re.search(r"[A-Za-z]", prefix):
                text = text[first_cjk_match.start() :].strip()

    if len(text) > limit:
        return text[:limit] + "..."
    return text


def check_content_integrity(result: "AnalysisResult") -> Tuple[bool, List[str]]:
    """
    Check mandatory fields for report content integrity.
    Returns (pass, missing_fields). Module-level for use by pipeline.
    """
    missing: List[str] = []
    if result.sentiment_score is None:
        missing.append("sentiment_score")
    advice = result.operation_advice
    if not advice or not isinstance(advice, str) or not advice.strip():
        missing.append("operation_advice")
    summary = result.analysis_summary
    if not summary or not isinstance(summary, str) or not summary.strip():
        missing.append("analysis_summary")
    dash = result.dashboard if isinstance(result.dashboard, dict) else {}
    core = dash.get("core_conclusion")
    core = core if isinstance(core, dict) else {}
    if not (core.get("one_sentence") or "").strip():
        missing.append("dashboard.core_conclusion.one_sentence")
    intel = dash.get("intelligence")
    intel = intel if isinstance(intel, dict) else None
    if intel is None or "risk_alerts" not in intel:
        missing.append("dashboard.intelligence.risk_alerts")
    if result.decision_type in ("buy", "hold"):
        battle = dash.get("battle_plan")
        battle = battle if isinstance(battle, dict) else {}
        sp = battle.get("sniper_points")
        sp = sp if isinstance(sp, dict) else {}
        stop_loss = sp.get("stop_loss")
        if stop_loss is None or (isinstance(stop_loss, str) and not stop_loss.strip()):
            missing.append("dashboard.battle_plan.sniper_points.stop_loss")
    return len(missing) == 0, missing


def apply_placeholder_fill(result: "AnalysisResult", missing_fields: List[str]) -> None:
    """Fill missing mandatory fields with placeholders (in-place)."""
    placeholder = get_placeholder_text(getattr(result, "report_language", "zh"))
    for field in missing_fields:
        if field == "sentiment_score":
            result.sentiment_score = 50
        elif field == "operation_advice":
            result.operation_advice = result.operation_advice or placeholder
        elif field == "analysis_summary":
            result.analysis_summary = result.analysis_summary or placeholder
        elif field == "dashboard.core_conclusion.one_sentence":
            if not result.dashboard:
                result.dashboard = {}
            if "core_conclusion" not in result.dashboard:
                result.dashboard["core_conclusion"] = {}
            result.dashboard["core_conclusion"]["one_sentence"] = (
                result.dashboard["core_conclusion"].get("one_sentence") or placeholder
            )
        elif field == "dashboard.intelligence.risk_alerts":
            if not result.dashboard:
                result.dashboard = {}
            if "intelligence" not in result.dashboard:
                result.dashboard["intelligence"] = {}
            if "risk_alerts" not in result.dashboard["intelligence"]:
                result.dashboard["intelligence"]["risk_alerts"] = []
        elif field == "dashboard.battle_plan.sniper_points.stop_loss":
            if not result.dashboard:
                result.dashboard = {}
            if "battle_plan" not in result.dashboard:
                result.dashboard["battle_plan"] = {}
            if "sniper_points" not in result.dashboard["battle_plan"]:
                result.dashboard["battle_plan"]["sniper_points"] = {}
            result.dashboard["battle_plan"]["sniper_points"]["stop_loss"] = placeholder


# ---------- chip_structure fallback (Issue #589) ----------

_CHIP_KEYS: tuple = ("profit_ratio", "avg_cost", "concentration", "chip_health", "source", "confidence", "method")


def _is_value_placeholder(v: Any) -> bool:
    """True if value is empty or placeholder (N/A, 数据缺失, etc.)."""
    if v is None:
        return True
    if isinstance(v, (int, float)) and v == 0:
        return True
    s = str(v).strip().lower()
    return s in ("", "n/a", "na", "数据缺失", "未知", "data unavailable", "unknown", "tbd")


def _safe_float(v: Any, default: Optional[float] = 0.0) -> Optional[float]:
    """Safely convert to float; return default on failure."""
    if v is None:
        return default
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        try:
            value = float(v)
            return default if math.isnan(value) or math.isinf(value) else value
        except (ValueError, TypeError):
            return default
    try:
        s = str(v).strip()
        if not s or s.lower() in {"n/a", "na", "none", "null", "unknown", "tbd", "数据缺失"}:
            return default
        value = float(s)
        return default if math.isnan(value) or math.isinf(value) else value
    except (TypeError, ValueError):
        return default


def _derive_chip_health(profit_ratio: float, concentration_90: float, language: str = "zh") -> str:
    """Derive chip_health from profit_ratio and concentration_90."""
    if profit_ratio >= 0.9:
        return localize_chip_health("警惕", language)
    if concentration_90 >= 0.25:
        return localize_chip_health("警惕", language)
    if concentration_90 <= 0.15 and profit_ratio >= 0.3:
        return localize_chip_health("健康", language)
    return localize_chip_health("一般", language)


def _classify_chip_source(source: Any) -> tuple[str, bool, str]:
    normalized = str(source or "").strip().lower()
    if not normalized or normalized in {"estimated", "estimated_ohlcv", "fallback", "unknown"}:
        return "estimated", True, "fallback_estimated"
    if normalized.startswith("estimated"):
        return "estimated", True, "fallback_estimated"
    return "real", False, "real_chip"


def _build_chip_structure_from_data(chip_data: Any, language: str = "zh") -> Dict[str, Any]:
    """Build chip_structure dict from ChipDistribution or dict."""
    if hasattr(chip_data, "profit_ratio"):
        pr = _safe_float(chip_data.profit_ratio, default=0.0) or 0.0
        raw_avg_cost = getattr(chip_data, "avg_cost", None)
        ac = _safe_float(raw_avg_cost, default=None)
        c90 = _safe_float(getattr(chip_data, "concentration_90", None), default=0.0) or 0.0
        source = getattr(chip_data, "source", "estimated")
        confidence = _safe_float(getattr(chip_data, "confidence", None), default=None)
        method = getattr(chip_data, "method", "")
    else:
        d = chip_data if isinstance(chip_data, dict) else {}
        pr = _safe_float(d.get("profit_ratio"), default=0.0) or 0.0
        raw_avg_cost = d.get("avg_cost")
        ac = _safe_float(raw_avg_cost, default=None)
        c90 = _safe_float(d.get("concentration_90", d.get("concentration")), default=0.0) or 0.0
        source = d.get("source", "estimated")
        confidence = _safe_float(d.get("confidence"), default=None)
        method = d.get("method", "")

    avg_cost_out: Any
    if isinstance(raw_avg_cost, str):
        avg_cost_out = raw_avg_cost
    elif ac in (None, 0.0):
        avg_cost_out = "N/A"
    else:
        avg_cost_out = ac

    confidence_out: Any
    if confidence is None:
        confidence_out = "N/A"
    else:
        confidence_out = f"{confidence:.0%}"

    chip_health = _derive_chip_health(pr, c90, language=language)
    source_value = source or "estimated"
    source_category, is_estimated, data_reliability = _classify_chip_source(source_value)
    return {
        "profit_ratio": f"{pr:.1%}",
        "profit_ratio_raw": pr,
        "avg_cost": avg_cost_out,
        "avg_cost_raw": ac if ac is not None else None,
        "concentration": f"{c90:.2%}",
        "concentration_raw": c90,
        "chip_health": chip_health,
        "source": source_value,
        "source_category": source_category,
        "is_estimated": is_estimated,
        "data_reliability": data_reliability,
        "confidence": confidence_out,
        "confidence_raw": confidence,
        "method": method or "truncated_gaussian",
    }


_INSTITUTION_KEYS: tuple = (
    "top10_holder_change",
    "top10_float_holder_change",
    "holder_num",
    "holder_num_change",
    "holder_num_end_date",
    "holder_num_ann_date",
    "institution_holding_change",
    "holder_structure_bias",
    "holder_structure_note",
)


def _derive_holder_structure_summary(institution_data: Dict[str, Any]) -> Dict[str, str]:
    """Derive human-readable holder structure interpretation from institution signals."""
    if not isinstance(institution_data, dict):
        return {}

    def _read_signal(key: str) -> Optional[float]:
        raw_value = institution_data.get(key)
        if raw_value is None:
            return None
        if isinstance(raw_value, str) and raw_value.strip().lower() in {
            "",
            "n/a",
            "na",
            "none",
            "null",
            "unknown",
            "tbd",
            "data unavailable",
            "数据缺失",
            "未知",
        }:
            return None
        return _safe_float(raw_value, default=None)

    top10_change = _read_signal("top10_holder_change")
    top10_label = "前十大"
    if top10_change is None:
        top10_change = _read_signal("top10_float_holder_change")
        top10_label = "前十大流通股东"
    holder_num_change = _read_signal("holder_num_change")

    def _sign(value: Optional[float]) -> int:
        if value is None:
            return 0
        if value > 0:
            return 1
        if value < 0:
            return -1
        return 0

    known_signal_count = sum(value is not None for value in (top10_change, holder_num_change))
    if known_signal_count == 0:
        return {}
    if known_signal_count == 1 and (top10_change == 0 or holder_num_change == 0):
        return {}

    top10_sign = _sign(top10_change)
    holder_sign = _sign(holder_num_change)

    if top10_sign == 0 and holder_sign == 0:
        return {
            "holder_structure_bias": "中性",
            "holder_structure_note": "前十大与股东户数均基本持平，机构/股东结构变化不明显。",
        }

    if top10_sign > 0 and holder_sign < 0:
        return {
            "holder_structure_bias": "集中",
            "holder_structure_note": f"{top10_label}净增持 + 户数下降，筹码向核心持有人集中，结构偏强。",
        }
    if top10_sign < 0 and holder_sign > 0:
        return {
            "holder_structure_bias": "分散",
            "holder_structure_note": f"{top10_label}净减持 + 户数上升，筹码扩散，需警惕大户退出后被更分散资金承接。",
        }
    if top10_sign < 0 and holder_sign < 0:
        return {
            "holder_structure_bias": "中性",
            "holder_structure_note": f"{top10_label}净减持 + 户数下降，存在大户退出但散户未显著接盘，筹码并非简单分散。",
        }
    if top10_sign > 0 and holder_sign > 0:
        return {
            "holder_structure_bias": "中性",
            "holder_structure_note": f"{top10_label}净增持 + 户数上升，说明增量资金并非只来自核心持有人，集中度改善有限。",
        }
    if top10_sign == 0 and holder_sign < 0:
        return {
            "holder_structure_bias": "集中",
            "holder_structure_note": f"{top10_label}基本持平 + 户数下降，筹码向存量持有人集中，但未见前十大明显增持。",
        }
    if top10_sign == 0 and holder_sign > 0:
        return {
            "holder_structure_bias": "分散",
            "holder_structure_note": f"{top10_label}基本持平 + 户数上升，筹码趋于分散，但未见前十大明显减持。",
        }
    if top10_sign > 0:
        return {
            "holder_structure_bias": "集中",
            "holder_structure_note": f"{top10_label}净增持，但户数变化缺失，暂按筹码偏集中理解。",
        }
    if top10_sign < 0:
        return {
            "holder_structure_bias": "分散",
            "holder_structure_note": f"{top10_label}净减持，但户数变化缺失，暂按筹码偏分散理解。",
        }
    if holder_sign < 0:
        return {
            "holder_structure_bias": "集中",
            "holder_structure_note": "股东户数下降，但前十大净变动缺失，暂按筹码偏集中理解。",
        }
    return {
        "holder_structure_bias": "分散",
        "holder_structure_note": "股东户数上升，但前十大净变动缺失，暂按筹码偏分散理解。",
    }


def fill_institution_structure_if_needed(result: "AnalysisResult", fundamental_context: Any) -> None:
    """Fill dashboard.data_perspective.institution_structure from fundamental_context in-place."""
    if not result or not isinstance(fundamental_context, dict):
        return
    try:
        institution_block = fundamental_context.get("institution") or {}
        institution_data = institution_block.get("data") if isinstance(institution_block, dict) else {}
        if not isinstance(institution_data, dict) or not institution_data:
            return
        if not result.dashboard:
            result.dashboard = {}
        dash = result.dashboard
        dp = dash.get("data_perspective") or {}
        dash["data_perspective"] = dp
        current = dp.get("institution_structure") or {}
        merged = dict(current)
        derived = _derive_holder_structure_summary(institution_data)

        def _is_institution_missing(value: Any) -> bool:
            if value is None:
                return True
            if isinstance(value, str):
                return value.strip().lower() in {
                    "",
                    "n/a",
                    "na",
                    "none",
                    "null",
                    "unknown",
                    "tbd",
                    "data unavailable",
                    "数据缺失",
                    "未知",
                }
            return False

        for key in _INSTITUTION_KEYS:
            source_value = institution_data.get(key)
            if key in derived:
                source_value = derived.get(key)
            if _is_institution_missing(merged.get(key)) and not _is_institution_missing(source_value):
                merged[key] = source_value
        if merged:
            dp["institution_structure"] = merged
            logger.info("[institution_structure] Filled institution fields from fundamental_context")
    except Exception as e:
        logger.warning("[institution_structure] Fill failed, skipping: %s", e)


def fill_chip_structure_if_needed(result: "AnalysisResult", chip_data: Any) -> None:
    """When chip_data exists, fill chip_structure placeholder fields from chip_data (in-place)."""
    if not result or not chip_data:
        return
    try:
        if not result.dashboard:
            result.dashboard = {}
        dash = result.dashboard
        dp = dash.get("data_perspective") or {}
        dash["data_perspective"] = dp
        cs = dp.get("chip_structure") or {}
        merged = dict(cs)
        filled = _build_chip_structure_from_data(
            chip_data,
            language=getattr(result, "report_language", "zh"),
        )
        for k in _CHIP_KEYS:
            if _is_value_placeholder(merged.get(k)) and not _is_value_placeholder(filled.get(k)):
                merged[k] = filled[k]
        for raw_key in (
            "profit_ratio_raw",
            "avg_cost_raw",
            "concentration_raw",
            "confidence_raw",
            "source_category",
            "is_estimated",
            "data_reliability",
        ):
            if raw_key not in merged and raw_key in filled:
                merged[raw_key] = filled[raw_key]
        current_source = merged.get("source")
        current_source_category, current_is_estimated, _ = _classify_chip_source(current_source)
        if current_source in (
            None,
            "",
            "N/A",
            "n/a",
            "NA",
            "na",
            "数据缺失",
            "真实",
            "real",
            "真实/estimated_ohlcv",
            "tushare_cyq_perf/tushare_cyq_chips",
            "tushare_cyq_perf/tushare_cyq_chips/akshare/estimated_ohlcv",
        ) or (current_is_estimated and filled.get("source_category") == "real"):
            merged["source"] = filled.get("source", "estimated")
            merged["source_category"] = filled.get("source_category", current_source_category)
            merged["is_estimated"] = filled.get("is_estimated", current_is_estimated)
            merged["data_reliability"] = filled.get("data_reliability")
        if _is_value_placeholder(merged.get("method")):
            merged["method"] = filled.get("method", "truncated_gaussian")
        if _is_value_placeholder(merged.get("confidence")):
            merged["confidence"] = filled.get("confidence", "N/A")
        dp["chip_structure"] = merged
        logger.info("[chip_structure] Filled chip fields from data source (Issue #589)")
        record_chip_chain_event(
            getattr(result, "stock_code", "-"),
            "fill",
            "ok",
            source=merged.get("source", filled.get("source", "-")),
            confidence=merged.get("confidence", filled.get("confidence")),
            method=merged.get("method", filled.get("method", "")),
            reason="filled_from_chip_data",
        )
    except Exception as e:
        logger.warning("[chip_structure] Fill failed, skipping: %s", e)


def record_chip_chain_event(
    stock_code: str,
    stage: str,
    status: str,
    *,
    source: str = "",
    confidence: Any = None,
    method: str = "",
    reason: str = "",
    extra: str = "",
) -> None:
    """Structured chip-chain audit log for source / fallback / data-abnormal separation."""
    confidence_text = "N/A"
    try:
        c = _safe_float(confidence, default=None)
        confidence_text = f"{c:.0%}" if c is not None else "N/A"
    except Exception:
        confidence_text = "N/A"
    logger.info(
        "[chip_chain] %s stage=%s status=%s source=%s confidence=%s method=%s reason=%s extra=%s",
        stock_code,
        stage,
        status,
        source or "-",
        confidence_text,
        method or "-",
        reason or "-",
        extra or "-",
    )


def analyze_chip_chain_snapshot(stock_code: str, chip_data: Any, result: Optional[Any] = None) -> Dict[str, Any]:
    """Run a real chip-chain snapshot and emit one-line audit summary."""
    if not chip_data:
        record_chip_chain_event(stock_code, "snapshot", "fallback", source="fallback_missing", reason="chip_data_missing")
        return {
            "stock_code": stock_code,
            "status": "fallback",
            "source": "fallback_missing",
            "confidence": "N/A",
            "method": "fallback_placeholder",
        }

    summary = _build_chip_structure_from_data(chip_data, language=getattr(result, "report_language", "zh") if result else "zh")
    src = summary.get("source", "-")
    conf = summary.get("confidence", "N/A")
    method = summary.get("method", "-")
    status = "ok"
    if src in ("fallback_missing", "数据缺失"):
        status = "fallback"
    elif src.startswith("estimated"):
        status = "estimated"
    record_chip_chain_event(stock_code, "snapshot", status, source=src, confidence=conf, method=method, reason="snapshot_ready", extra=f"health={summary.get('chip_health','-')}")
    return summary


_PRICE_POS_KEYS = ("ma5", "ma10", "ma20", "bias_ma5", "bias_status", "current_price", "support_level", "resistance_level")


def fill_price_position_if_needed(
    result: "AnalysisResult",
    trend_result: Any = None,
    realtime_quote: Any = None,
) -> None:
    """Fill missing price_position fields from trend_result / realtime data (in-place)."""
    if not result:
        return
    try:
        if not result.dashboard:
            result.dashboard = {}
        dash = result.dashboard
        dp = dash.get("data_perspective") or {}
        dash["data_perspective"] = dp
        pp = dp.get("price_position") or {}

        computed: Dict[str, Any] = {}
        if trend_result:
            tr = trend_result if isinstance(trend_result, dict) else (
                trend_result.__dict__ if hasattr(trend_result, "__dict__") else {}
            )
            computed["ma5"] = tr.get("ma5")
            computed["ma10"] = tr.get("ma10")
            computed["ma20"] = tr.get("ma20")
            computed["bias_ma5"] = tr.get("bias_ma5")
            computed["current_price"] = tr.get("current_price")
            support_levels = tr.get("support_levels") or []
            resistance_levels = tr.get("resistance_levels") or []
            if support_levels:
                computed["support_level"] = support_levels[0]
            if resistance_levels:
                computed["resistance_level"] = resistance_levels[0]
        if realtime_quote:
            rq = realtime_quote if isinstance(realtime_quote, dict) else (
                realtime_quote.to_dict() if hasattr(realtime_quote, "to_dict") else {}
            )
            if _is_value_placeholder(computed.get("current_price")):
                computed["current_price"] = rq.get("price")

        filled = False
        for k in _PRICE_POS_KEYS:
            if _is_value_placeholder(pp.get(k)) and not _is_value_placeholder(computed.get(k)):
                pp[k] = computed[k]
                filled = True
        if filled:
            dp["price_position"] = pp
            logger.info("[price_position] Filled placeholder fields from computed data")
    except Exception as e:
        logger.warning("[price_position] Fill failed, skipping: %s", e)


def get_stock_name_multi_source(
    stock_code: str,
    context: Optional[Dict] = None,
    data_manager=None,
) -> str:
    """
    多来源获取股票中文名称

    获取策略（按优先级）：
    1. 从传入的 context 中获取（realtime 数据）
    2. 从静态映射表 STOCK_NAME_MAP 获取
    3. 从 DataFetcherManager 获取（各数据源）
    4. 返回默认名称（股票+代码）
    """
    if context:
        if context.get("stock_name"):
            name = context["stock_name"]
            if name and not name.startswith("股票"):
                return name

        if "realtime" in context and context["realtime"].get("name"):
            return context["realtime"]["name"]

    if stock_code in STOCK_NAME_MAP:
        return STOCK_NAME_MAP[stock_code]

    if data_manager is None:
        try:
            from data_provider.base import DataFetcherManager

            data_manager = DataFetcherManager()
        except Exception as e:
            logger.debug(f"无法初始化 DataFetcherManager: {e}")

    if data_manager:
        try:
            name = data_manager.get_stock_name(stock_code)
            if name:
                STOCK_NAME_MAP[stock_code] = name
                return name
        except Exception as e:
            logger.debug(f"从数据源获取股票名称失败: {e}")

    return f"股票{stock_code}"
