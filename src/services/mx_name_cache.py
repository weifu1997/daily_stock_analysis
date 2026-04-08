# -*- coding: utf-8 -*-
"""妙想预选池股票名称缓存。"""

from __future__ import annotations

from threading import RLock
from typing import Dict, Optional

from src.data.stock_mapping import is_meaningful_stock_name
from src.services.stock_code_utils import normalize_code

_LOCK = RLock()
_CODE_TO_NAME: Dict[str, str] = {}


def _normalize_stock_code(code: str) -> Optional[str]:
    normalized = normalize_code(code)
    if not normalized:
        return None
    return normalized


def cache_stock_name(stock_code: str, stock_name: Optional[str]) -> Optional[str]:
    """Cache a stock name from MX or other upstream sources."""
    normalized_code = _normalize_stock_code(stock_code)
    if not normalized_code:
        return None
    if not is_meaningful_stock_name(stock_name, normalized_code):
        return None

    name = str(stock_name).strip()
    with _LOCK:
        _CODE_TO_NAME[normalized_code] = name
    return name


def get_cached_stock_name(stock_code: str) -> Optional[str]:
    """Return cached stock name if available."""
    normalized_code = _normalize_stock_code(stock_code)
    if not normalized_code:
        return None
    with _LOCK:
        return _CODE_TO_NAME.get(normalized_code)


def clear_cached_stock_names() -> None:
    with _LOCK:
        _CODE_TO_NAME.clear()
