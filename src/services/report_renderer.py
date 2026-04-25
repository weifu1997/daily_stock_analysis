# -*- coding: utf-8 -*-
"""
===================================
Report Engine - Jinja2 Report Renderer
===================================

Renders reports from Jinja2 templates. Falls back to caller's logic on template
missing or render error. Template path is relative to project root.
Any expensive data preparation should be injected by the caller via extra_context.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.analysis.candidate_layers.distribution import build_l2_report_summary
from src.analyzer import AnalysisResult
from src.config import get_config
from src.report_language import (
    get_localized_stock_name,
    get_report_labels,
    get_result_guardrail_messages,
    get_result_guardrail_traces,
    get_signal_level,
    infer_decision_type_from_advice,
    localize_chip_health,
    localize_normalization_reason_code,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)

logger = logging.getLogger(__name__)


def _escape_md(text: str) -> str:
    """Escape markdown special chars (*ST etc)."""
    if not text:
        return ""
    return text.replace("*", "\\*").replace("_", "\\_")


def _clean_sniper_value(val: Any) -> str:
    """Format sniper point value for display (strip label prefixes)."""
    if val is None:
        return "N/A"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).strip() if val else ""
    if not s or s == "N/A":
        return s or "N/A"
    prefixes = [
        "理想买入点：", "次优买入点：", "止损位：", "目标位：",
        "理想买入点:", "次优买入点:", "止损位:", "目标位:",
        "Ideal Entry:", "Secondary Entry:", "Stop Loss:", "Target:",
    ]
    for prefix in prefixes:
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


_PROMPT_PLACEHOLDER_RE = re.compile(r"{{\s*([^{}]+?)\s*}}")
_PROMPT_BLOCK_COMMENT_RE = re.compile(r"{#.*?#}", re.DOTALL)
_ALLOWED_TEMPLATE_ROOTS = {
    "report_date",
    "labels",
    "results",
    "enriched",
    "result",
    "e",
    "core",
    "intel",
    "battle",
    "data_persp",
    "decision_context",
    "portfolio_context",
    "portfolio_contexts",
    "report_quality",
    "report_quality_map",
    "candidate_score_map",
    "candidate_score",
    "candidate_layer_summary",
    "execution_plan_map",
    "execution_plan",
    "risk_item",
    "report_decision_map",
    "history_by_code",
    "mx_enrichment",
    "report_timestamp",
    "summary_only",
    "buy_count",
    "sell_count",
    "hold_count",
    "report_language",
    "localize_operation_advice",
    "localize_trend_prediction",
    "localize_chip_health",
    "localize_normalization_reason_code",
    "get_result_guardrail_messages",
    "get_result_guardrail_traces",
    "clean_sniper",
    "failed_checks",
    "market_snapshot",
    "execution_policy_note",
    # Template-local helpers/variables used inside Jinja control flow
    "adj_data",
    "checklist",
    "chip_data",
    "guardrail_traces",
    "institution_data",
    "position",
    "price_data",
    "trend_data",
    "vol_data",
    "ns",
}


def _load_template_text(template_name: str) -> Optional[str]:
    template_path = _resolve_templates_dir() / template_name
    if not template_path.exists():
        return None
    return template_path.read_text(encoding="utf-8")


def _extract_template_roots(template_text: str) -> Set[str]:
    try:
        from jinja2 import Environment, meta
    except ImportError:
        return set()

    env = Environment()
    parsed = env.parse(template_text)
    return set(meta.find_undeclared_variables(parsed))


def _warn_missing_template_context_keys(template_name: str, template_text: str, context: Dict[str, Any]) -> None:
    try:
        from jinja2 import Environment, meta
    except ImportError:
        return

    env = Environment()
    parsed = env.parse(template_text)
    undeclared = sorted(
        name
        for name in meta.find_undeclared_variables(parsed)
        if name not in context and name not in _ALLOWED_TEMPLATE_ROOTS
    )
    if undeclared:
        logger.warning("Template %s references missing context roots: %s", template_name, ", ".join(undeclared))


CONTRACT_FIELD_MAPPING: Dict[str, str] = {
    "stock_name": "AnalysisResult.stock_name",
    "sentiment_score": "AnalysisResult.sentiment_score",
    "trend_prediction": "AnalysisResult.trend_prediction",
    "operation_advice": "AnalysisResult.operation_advice",
    "decision_type": "AnalysisResult.decision_type",
    "confidence_level": "AnalysisResult.confidence_level",
    "dashboard": "AnalysisResult.dashboard",
    "analysis_summary": "AnalysisResult.analysis_summary",
    "key_points": "AnalysisResult.key_points",
    "risk_warning": "AnalysisResult.risk_warning",
    "buy_reason": "AnalysisResult.buy_reason",
    "trend_analysis": "AnalysisResult.trend_analysis",
    "short_term_outlook": "AnalysisResult.short_term_outlook",
    "medium_term_outlook": "AnalysisResult.medium_term_outlook",
    "technical_analysis": "AnalysisResult.technical_analysis",
    "ma_analysis": "AnalysisResult.ma_analysis",
    "volume_analysis": "AnalysisResult.volume_analysis",
    "pattern_analysis": "AnalysisResult.pattern_analysis",
    "fundamental_analysis": "AnalysisResult.fundamental_analysis",
    "sector_position": "AnalysisResult.sector_position",
    "company_highlights": "AnalysisResult.company_highlights",
    "news_summary": "AnalysisResult.news_summary",
    "market_sentiment": "AnalysisResult.market_sentiment",
    "hot_topics": "AnalysisResult.hot_topics",
    "search_performed": "AnalysisResult.search_performed",
    "data_sources": "AnalysisResult.data_sources",
    "prompt_version": "AnalysisResult.prompt_version",
    "report_language": "AnalysisResult.report_language",
    "model_used": "AnalysisResult.model_used",
    "current_price": "AnalysisResult.current_price",
    "change_pct": "AnalysisResult.change_pct",
}


def _load_contract_field_mapping() -> Dict[str, str]:
    return dict(CONTRACT_FIELD_MAPPING)


def _write_contract_doc(template_names: List[str]) -> None:
    contract_dir = _resolve_templates_dir().parent / "docs" / "contracts"
    contract_dir.mkdir(parents=True, exist_ok=True)
    mapping = _load_contract_field_mapping()
    lines = [
        "# Prompt / Result Contract Mapping",
        "",
        "This document is generated from current templates and should remain in sync with report templates.",
        "",
        "## Template placeholders",
    ]
    for template_name in template_names:
        text = _load_template_text(template_name)
        if text is None:
            continue
        roots = sorted(_extract_template_roots(text))
        lines.append(f"\n### {template_name}")
        for root in roots:
            mapped = mapping.get(root, "(unmapped)")
            lines.append(f"- `{root}` -> `{mapped}`")
    lines.extend([
        "",
        "## AnalysisResult fields tracked",
    ])
    for source in sorted(set(mapping.values())):
        lines.append(f"- `{source}`")
    contract_path = contract_dir / "prompt-to-result-mapping.md"
    contract_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve_templates_dir() -> Path:
    """Resolve template directory relative to project root."""
    config = get_config()
    base = Path(__file__).resolve().parent.parent.parent
    templates_dir = Path(config.report_templates_dir)
    if not templates_dir.is_absolute():
        return base / templates_dir
    return templates_dir


def render(
    platform: str,
    results: List[AnalysisResult],
    report_date: Optional[str] = None,
    summary_only: bool = False,
    extra_context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Render report using Jinja2 template.

    Args:
        platform: One of: markdown, wechat, brief
        results: List of AnalysisResult
        report_date: Report date string (default: today)
        summary_only: Whether to output summary only
        extra_context: Additional template context

    Returns:
        Rendered string, or None on error (caller should fallback).
    """
    from datetime import datetime

    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError:
        logger.warning("jinja2 not installed, report renderer disabled")
        return None

    if report_date is None:
        report_date = datetime.now().strftime("%Y-%m-%d")

    templates_dir = _resolve_templates_dir()
    template_name = f"report_{platform}.j2"
    template_path = templates_dir / template_name
    if not template_path.exists():
        logger.debug("Report template not found: %s", template_path)
        return None
    template_text = template_path.read_text(encoding="utf-8")
    _warn_missing_template_context_keys(template_name, template_text, {"report_date": report_date, "results": results, "summary_only": summary_only, "extra_context": extra_context})

    report_language = normalize_report_language(
        (extra_context or {}).get("report_language")
        or next(
            (getattr(result, "report_language", None) for result in results if getattr(result, "report_language", None)),
            None,
        )
        or getattr(get_config(), "report_language", "zh")
    )
    labels = get_report_labels(report_language)

    # Build template context with pre-computed signal levels (sorted by score)
    sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)
    sorted_enriched = []
    for r in sorted_results:
        st, se, _ = get_signal_level(r.operation_advice, r.sentiment_score, report_language)
        rn = get_localized_stock_name(r.name, r.code, report_language)
        sorted_enriched.append({
            "result": r,
            "signal_text": st,
            "signal_emoji": se,
            "stock_name": _escape_md(rn),
            "localized_operation_advice": f"{localize_operation_advice(r.operation_advice, report_language)}（次日执行建议）",
            "localized_trend_prediction": localize_trend_prediction(r.trend_prediction, report_language),
        })

    buy_count = sum(
        1 for r in results
        if infer_decision_type_from_advice(getattr(r, "operation_advice", None)) == "buy"
    )
    sell_count = sum(
        1 for r in results
        if infer_decision_type_from_advice(getattr(r, "operation_advice", None)) == "sell"
    )
    hold_count = sum(
        1 for r in results
        if infer_decision_type_from_advice(getattr(r, "operation_advice", None)) == "hold"
    )

    report_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def failed_checks(checklist: List[str]) -> List[str]:
        return [c for c in (checklist or []) if c.startswith("❌") or c.startswith("⚠️")]

    mx_enrichment = (extra_context or {}).get("mx_enrichment") if extra_context else None
    portfolio_contexts = (extra_context or {}).get("portfolio_contexts") if extra_context else None
    candidate_score_map = (extra_context or {}).get("candidate_score_map") if extra_context else None
    candidate_layer_summary = (extra_context or {}).get("candidate_layer_summary") if extra_context else None
    if candidate_layer_summary is None and isinstance(candidate_score_map, dict) and candidate_score_map:
        candidate_layer_summary = build_l2_report_summary(candidate_score_map)
    context: Dict[str, Any] = {
        "portfolio_contexts": portfolio_contexts,
        "report_date": report_date,
        "report_timestamp": report_timestamp,
        "results": sorted_results,
        "enriched": sorted_enriched,  # Sorted by sentiment_score desc
        "summary_only": summary_only,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "hold_count": hold_count,
        "labels": labels,
        "report_language": report_language,
        "escape_md": _escape_md,
        "clean_sniper": _clean_sniper_value,
        "failed_checks": failed_checks,
        "history_by_code": {},
        "localize_operation_advice": localize_operation_advice,
        "localize_trend_prediction": localize_trend_prediction,
        "localize_chip_health": localize_chip_health,
        "localize_normalization_reason_code": localize_normalization_reason_code,
        "get_result_guardrail_messages": get_result_guardrail_messages,
        "get_result_guardrail_traces": get_result_guardrail_traces,
        "mx_enrichment": mx_enrichment,
        "candidate_layer_summary": candidate_layer_summary,
        "execution_policy_note": "以下信号为日终分析结果，默认用于次一交易日执行，不代表当晚立即交易。",
    }
    if extra_context:
        safe_extra_context = dict(extra_context)
        safe_extra_context.pop("labels", None)
        safe_extra_context.pop("report_language", None)
        context.update(safe_extra_context)

    try:
        env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(default=False),
        )
        template = env.get_template(template_name)
        return template.render(**context)
    except Exception as e:
        logger.warning("Report render failed for %s: %s", template_name, e)
        return None
