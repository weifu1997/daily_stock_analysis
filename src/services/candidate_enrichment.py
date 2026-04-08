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
            return {"mx_enabled": False, "mx_event_score": 0.0, "mx_theme_tags": [], "mx_risk_flags": [], "mx_events": []}
        return {
            "mx_enabled": True,
            "mx_event_score": signal.event_score,
            "mx_theme_tags": signal.theme_tags,
            "mx_risk_flags": signal.risk_flags,
            "mx_events": [ev.__dict__ for ev in signal.events],
            "mx_data_summary": self._query_data_summary(code, name),
        }

    def _fetch_signal(self, code: str, name: str) -> Optional[MxSignal]:
        if not self.search_adapter:
            return None
        try:
            return self.search_adapter.enrich_stock(code=code, name=name)
        except Exception:
            return None

    def _query_data_summary(self, code: str, name: str) -> Dict[str, Any]:
        if not self.mx_client or not getattr(self.mx_client, 'enabled', False):
            return {"mx_data_enabled": False}
        question = f"{name or code} {code} 财务指标 估值 ROE 净利润 资产负债率 经营现金流，提炼成简短摘要"
        payload = {"toolQuery": question}
        try:
            resp = self.mx_client.query(payload)
            if not getattr(resp, 'ok', False):
                return {"mx_data_enabled": True, "mx_data_ok": False, "error": getattr(resp, 'error', None)}
            raw = resp.data or {}
            return {
                "mx_data_enabled": True,
                "mx_data_ok": True,
                "raw": raw,
            }
        except Exception as exc:
            logger.warning("mx-data query failed for %s %s: %s", code, name, exc)
            return {"mx_data_enabled": True, "mx_data_ok": False, "error": str(exc)}
