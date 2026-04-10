# -*- coding: utf-8 -*-
"""候选池增强服务：把妙想信号标准化为 screen/report 可用结构。"""

import logging
from typing import Any, Dict, Iterable, List, Optional

from src.integrations.mx.models import MxSignal

logger = logging.getLogger(__name__)


class CandidateEnrichmentService:
    def __init__(self, search_adapter=None, mx_client=None):
        self.search_adapter = search_adapter
        self.mx_client = mx_client

    def enrich_candidates(self, candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """候选池阶段只补轻量信号，不做逐只 mx-data 摘要查询。"""
        enriched: List[Dict[str, Any]] = []
        for item in candidates:
            code = str(item.get("code", "")).strip()
            name = str(item.get("name", "")).strip()
            enriched_item = dict(item)
            signal = self._fetch_signal(code, name)
            enriched_item["mx_event_score"] = signal.event_score if signal else 0.0
            enriched_item["mx_theme_tags"] = signal.theme_tags if signal else []
            enriched_item["mx_risk_flags"] = signal.risk_flags if signal else []
            enriched_item["mx_events"] = [ev.__dict__ for ev in (signal.events if signal else [])]
            enriched_item["mx_data_summary"] = {
                "mx_data_enabled": bool(self.mx_client and getattr(self.mx_client, 'enabled', False)),
                "mx_data_skipped": True,
                "reason": "candidate_pool_stage_skip",
            }
            enriched.append(enriched_item)
        return enriched

    def build_report_summary(self, code: str, name: str = "") -> Dict[str, Any]:
        signal = self._fetch_signal(code, name)
        if not signal:
            return {
                "mx_enabled": False,
                "mx_event_score": 0.0,
                "mx_theme_tags": [],
                "mx_risk_flags": [],
                "mx_events": [],
                "financial_filter_summary": {"enabled": False},
            }
        mx_data_summary = self._query_data_summary(code, name)
        financial_filter_summary = self._build_financial_filters(mx_data_summary.get("financial_summary") if isinstance(mx_data_summary, dict) else None)
        return {
            "mx_enabled": True,
            "mx_event_score": signal.event_score,
            "mx_theme_tags": signal.theme_tags,
            "mx_risk_flags": signal.risk_flags,
            "mx_events": [ev.__dict__ for ev in signal.events],
            "mx_data_summary": mx_data_summary,
            "financial_filter_summary": financial_filter_summary,
        }

    def _build_financial_filters(self, financial_summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Build a tiny financial filter block for downstream ranking / screening."""
        if not isinstance(financial_summary, dict) or not financial_summary:
            return {"enabled": False}

        total_revenue = financial_summary.get("total_revenue")
        n_income_attr_p = financial_summary.get("n_income_attr_p")
        n_income = financial_summary.get("n_income")
        basic_eps = financial_summary.get("basic_eps")
        operate_profit = financial_summary.get("operate_profit")
        total_profit = financial_summary.get("total_profit")
        rd_exp = financial_summary.get("rd_exp")
        revenue_yoy = financial_summary.get("revenue_yoy")
        profit_yoy = financial_summary.get("profit_yoy")
        roe = financial_summary.get("roe")

        flags = []
        if isinstance(profit_yoy, (int, float)) and profit_yoy < 0:
            flags.append("profit_yoy_negative")
        if isinstance(revenue_yoy, (int, float)) and revenue_yoy < 0:
            flags.append("revenue_yoy_negative")
        if isinstance(n_income_attr_p, (int, float)) and n_income_attr_p < 0:
            flags.append("parent_profit_negative")
        if isinstance(operate_profit, (int, float)) and operate_profit < 0:
            flags.append("operate_profit_negative")
        if isinstance(total_profit, (int, float)) and total_profit < 0:
            flags.append("total_profit_negative")

        score = 0
        for val, threshold in ((revenue_yoy, 0), (profit_yoy, 0), (roe, 10)):
            if isinstance(val, (int, float)) and val > threshold:
                score += 1
        if isinstance(n_income_attr_p, (int, float)) and n_income_attr_p > 0:
            score += 1
        if isinstance(basic_eps, (int, float)) and basic_eps > 0:
            score += 1
        if isinstance(rd_exp, (int, float)) and rd_exp > 0:
            score += 1

        return {
            "enabled": True,
            "score": score,
            "flags": flags,
            "snapshot": {
                "total_revenue": total_revenue,
                "n_income_attr_p": n_income_attr_p,
                "n_income": n_income,
                "basic_eps": basic_eps,
                "operate_profit": operate_profit,
                "total_profit": total_profit,
                "rd_exp": rd_exp,
                "revenue_yoy": revenue_yoy,
                "profit_yoy": profit_yoy,
                "roe": roe,
            },
        }
