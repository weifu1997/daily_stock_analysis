# -*- coding: utf-8 -*-
"""妙想接入层标准数据结构。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MxResponse:
    ok: bool
    data: Any = None
    error: Optional[str] = None
    source: str = "mx"
    latency_ms: Optional[int] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass
class MxEvent:
    title: str
    summary: str = ""
    code: str = ""
    source: str = ""
    url: str = ""
    published_at: str = ""
    tags: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)


@dataclass
class MxSignal:
    code: str
    name: str = ""
    event_score: float = 0.0
    theme_tags: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    events: List[MxEvent] = field(default_factory=list)
    source: str = "mx"
    extra: Dict[str, Any] = field(default_factory=dict)
