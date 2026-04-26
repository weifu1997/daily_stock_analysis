# -*- coding: utf-8 -*-
"""Shared MX preselect configuration and helpers.

Keep the preselect profiles, exclusion tokens, and profile resolution logic in
one place so main entrypoints and dispatch helpers stay in sync.
"""

from __future__ import annotations

from typing import List, Optional

MX_PRESELECT_PROFILES = {
    "trend": "A股 正常交易 近期突破 量价配合 成交量放大 排除ST 排除停牌",
    "fundamental": "A股 正常交易 非ST 非停牌 低估值 高ROE 业绩稳定 经营现金流良好 财务健康 排除科创板 排除创业板 排除北交所",
    "basic": "A股 正常交易 排除ST 排除停牌 排除异常标的",
}

MX_PRESELECT_EXCLUDE_TOKENS = (
    "排除ST",
    "非ST",
    "排除停牌",
    "非停牌",
    "排除科创板",
    "排除创业板",
    "排除北交所",
)

MX_PRESELECT_REQUIRED_TOKENS = (
    "A股",
    "正常交易",
)


def resolve_mx_profile_query(profile: Optional[str]) -> Optional[str]:
    """Resolve a named MX preselect profile to its query string."""
    if not profile:
        return None
    return MX_PRESELECT_PROFILES.get(profile.strip().lower())


def validate_preselect_query(query: str) -> List[str]:
    """Validate a preselect query string against required and excluded tokens.

    Returns a list of violation messages. Empty list means valid.
    """
    violations: List[str] = []
    if not query or not query.strip():
        violations.append("query is empty")
        return violations

    q = query.strip()
    for token in MX_PRESELECT_REQUIRED_TOKENS:
        if token not in q:
            violations.append(f"missing required token: {token}")

    # EXCLUDE_TOKENS: check that at least one variant of each exclusion is present
    # Group variants by semantic meaning (e.g., "排除ST" and "非ST" both mean exclude ST)
    exclusion_groups = [
        ("ST", ["排除ST", "非ST"]),
        ("停牌", ["排除停牌", "非停牌"]),
        ("科创板", ["排除科创板"]),
        ("创业板", ["排除创业板"]),
        ("北交所", ["排除北交所"]),
    ]
    for name, variants in exclusion_groups:
        if not any(v in q for v in variants):
            violations.append(f"missing exclusion token for {name}")

    return violations
