# -*- coding: utf-8 -*-
"""妙想搜索结果适配到项目内候选池增强结构。"""

from typing import Any, Dict, Iterable, List, Optional

from .models import MxEvent, MxSignal


class MxSearchAdapter:
    def __init__(self, client):
        self.client = client

    def enrich_stock(self, code: str, name: str = "", keywords: Optional[Iterable[str]] = None) -> MxSignal:
        if not self.client or not getattr(self.client, "enabled", False):
            return MxSignal(code=code, name=name, source="mx", extra={"disabled": True})

        q = " ".join([code, name, *(keywords or [])]).strip()
        resp = self.client.search(q, code=code, name=name)
        if not resp.ok:
            return MxSignal(code=code, name=name, source="mx", extra={"response_error": resp.error})

        raw = resp.data or {}
        items = raw.get("items") if isinstance(raw, dict) else raw
        events: List[MxEvent] = []
        if isinstance(items, list):
            for item in items[:10]:
                if not isinstance(item, dict):
                    continue
                events.append(MxEvent(
                    title=str(item.get("title", "")),
                    summary=str(item.get("summary", "")),
                    code=str(item.get("code", code)),
                    source=str(item.get("source", "mx")),
                    url=str(item.get("url", "")),
                    published_at=str(item.get("published_at", "")),
                    tags=list(item.get("tags", []) or []),
                    risk_flags=list(item.get("risk_flags", []) or []),
                ))

        theme_tags = []
        risk_flags = []
        for ev in events:
            theme_tags.extend(ev.tags)
            risk_flags.extend(ev.risk_flags)

        event_score = min(100.0, max(0.0, len(events) * 5.0 + (5.0 if theme_tags else 0.0) - (5.0 if risk_flags else 0.0)))
        return MxSignal(
            code=code,
            name=name,
            event_score=event_score,
            theme_tags=sorted(set(theme_tags)),
            risk_flags=sorted(set(risk_flags)),
            events=events,
            source="mx",
            extra={"raw": raw},
        )
