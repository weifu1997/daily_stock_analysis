# -*- coding: utf-8 -*-
"""Pure search helper utilities extracted from ``search_service``.

These helpers are intentionally dependency-light so they can be reused by the
service layer without pulling in provider classes or runtime configuration.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional, Tuple

from data_provider.us_index_mapping import is_us_index_code

DATE_FIELD_CANDIDATES = (
    "published_date",
    "publishedDate",
    "pubdate",
    "datePublished",
    "date",
    "age",
    "page_age",
)

_RELATIVE_PATTERNS = (
    re.compile(r"\b\d{1,3}\s*(?:minute|minutes|min|mins|hour|hours|day|days|week|weeks|month|months|year|years)\s+ago\b", re.IGNORECASE),
    re.compile(r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b", re.IGNORECASE),
    re.compile(r"\b\d{4}\.\d{1,2}\.\d{1,2}\b", re.IGNORECASE),
    re.compile(r"\d+\s*(?:分钟前|小时前|天前|周前|个月前|月前|年前)", re.IGNORECASE),
    re.compile(r"\d{4}年\d{1,2}月\d{1,2}日", re.IGNORECASE),
    re.compile(r"(?:日期|时间|发表于|发布于)\s*[:：]?\s*(\d{8})", re.IGNORECASE),
)
_URL_YMD8_PATTERN = re.compile(r"(?:^|[^\d])(20\d{6})(?:[^\d]|$)")
_URL_PREFIXED_YMD8_PATTERN = re.compile(r"[/_=-]?[a-z]?(20\d{6})(?:\d{2,}|[^\d]|$)", re.IGNORECASE)
_CHINESE_TEXT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_US_STOCK_RE = re.compile(r"^[A-Za-z]{1,5}(\.[A-Za-z])?$")
_A_ETF_PREFIXES = ("51", "52", "56", "58", "15", "16", "18")
_ETF_NAME_KEYWORDS = ("ETF", "FUND", "TRUST", "INDEX", "TRACKER", "UNIT")


def cache_key(query: str, max_results: int, days: int) -> str:
    """Build a cache key from query parameters."""
    return f"{query}|{max_results}|{days}"


def provider_request_size(max_results: int, *, oversample_factor: int, oversample_max: int) -> int:
    """Apply light overfetch before time filtering to avoid sparse outputs."""
    target = max(1, int(max_results))
    return max(target, min(target * oversample_factor, oversample_max))


def summarize_raw_keys(raw: Any, *, max_keys: int = 16, max_depth: int = 2) -> str:
    """Summarize raw provider keys for diagnostics."""
    if not isinstance(raw, dict):
        return ""

    keys: list[str] = []
    seen: set[str] = set()

    def _append(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            keys.append(value)

    def _walk(obj: Any, prefix: str = "", depth: int = 0) -> None:
        if not isinstance(obj, dict) or depth > max_depth or len(keys) >= max_keys:
            return
        for key, value in obj.items():
            key_name = str(key)
            path = f"{prefix}{key_name}" if not prefix else f"{prefix}.{key_name}"
            _append(path)
            if len(keys) >= max_keys:
                return
            if isinstance(value, dict):
                _walk(value, path, depth + 1)
            elif isinstance(value, list):
                for idx, item in enumerate(value[:3]):
                    if isinstance(item, dict):
                        _walk(item, f"{path}[]", depth + 1)
                    elif item is not None and not isinstance(item, (str, bytes)):
                        _append(f"{path}[{idx}]")
                    if len(keys) >= max_keys:
                        return

    _walk(raw)
    if not keys:
        return ""
    if len(keys) > max_keys:
        keys = keys[:max_keys] + ["..."]
    return ",".join(keys)


def extract_date_value(item: dict) -> Any:
    """Extract the first non-empty date value from a provider result dict."""

    def _scan(obj: Any, depth: int = 0) -> Any:
        if not isinstance(obj, dict):
            return None
        for field in DATE_FIELD_CANDIDATES:
            val = obj.get(field)
            if val is not None and str(val).strip():
                return val
        if depth >= 2:
            return None
        for value in obj.values():
            if isinstance(value, dict):
                found = _scan(value, depth + 1)
                if found is not None:
                    return found
            elif isinstance(value, list):
                for item_value in value:
                    if isinstance(item_value, dict):
                        found = _scan(item_value, depth + 1)
                        if found is not None:
                            return found
        return None

    return _scan(item)


def extract_date_text_fallback(*texts: Any) -> Optional[str]:
    """Extract a date-like snippet from free text when structured fields are empty."""
    for text in texts:
        if text is None:
            continue
        normalized = str(text).strip()
        if not normalized:
            continue
        window = normalized[:200]
        for pattern in _RELATIVE_PATTERNS:
            match = pattern.search(window)
            if not match:
                continue
            if match.lastindex:
                return next((group.strip() for group in match.groups() if group), None)
            return match.group(0).strip()
        if "://" in normalized:
            url_match = _URL_YMD8_PATTERN.search(normalized) or _URL_PREFIXED_YMD8_PATTERN.search(normalized)
            if url_match:
                return url_match.group(1)
    return None


def parse_relative_news_date(text: str, now: datetime) -> Optional[date]:
    """Parse common Chinese/English relative-time strings."""
    raw = (text or "").strip()
    if not raw:
        return None

    normalized = raw.lower().replace("\u00a0", " ")
    normalized = normalized.replace("发布于", "").replace("发表于", "").replace("时间", "").replace("日期", "")
    normalized = re.sub(r"\s+", " ", normalized).strip()

    local_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    local_tz = local_now.astimezone().tzinfo or timezone.utc

    def _shift_days(amount: int) -> date:
        return (local_now - timedelta(days=amount)).astimezone(local_tz).date()

    if normalized in {"today", "just now", "now", "今天", "刚刚", "今日"}:
        return local_now.astimezone(local_tz).date()
    if normalized in {"yesterday", "昨天"}:
        return _shift_days(1)
    if normalized in {"the day before yesterday", "前天"}:
        return _shift_days(2)

    cn_patterns = (
        (r"^(\d+)\s*分钟前$", 0),
        (r"^(\d+)\s*小时前$", 0),
        (r"^(\d+)\s*天前$", 1),
        (r"^(\d+)\s*周前$", 7),
        (r"^(\d+)\s*个月前$", 30),
        (r"^(\d+)\s*月前$", 30),
        (r"^(\d+)\s*年前$", 365),
    )
    for pattern, scale in cn_patterns:
        match = re.match(pattern, normalized)
        if not match:
            continue
        amount = int(match.group(1))
        if "分钟" in pattern or "小时" in pattern:
            return local_now.astimezone(local_tz).date()
        return (local_now - timedelta(days=amount * scale)).astimezone(local_tz).date()

    en_patterns = (
        (r"^(\d+)\s*minute(?:s)?\s+ago$", 0),
        (r"^(\d+)\s*hour(?:s)?\s+ago$", 0),
        (r"^(\d+)\s*day(?:s)?\s+ago$", 1),
        (r"^(\d+)\s*week(?:s)?\s+ago$", 7),
        (r"^(\d+)\s*month(?:s)?\s+ago$", 30),
        (r"^(\d+)\s*year(?:s)?\s+ago$", 365),
    )
    for pattern, scale in en_patterns:
        match = re.match(pattern, normalized)
        if not match:
            continue
        amount = int(match.group(1))
        if scale == 0:
            return local_now.astimezone(local_tz).date()
        return (local_now - timedelta(days=amount * scale)).astimezone(local_tz).date()

    return None


def normalize_news_publish_date_with_reason(
    value: Any, now: Optional[datetime] = None
) -> Tuple[Optional[date], str]:
    """Normalize date value and return (date, reason)."""
    if value is None:
        return None, "no_field"
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            local_tz = now.astimezone().tzinfo if now else (datetime.now().astimezone().tzinfo or timezone.utc)
            return value.astimezone(local_tz).date(), "ok"
        return value.date(), "ok"
    if isinstance(value, date):
        return value, "ok"

    text = str(value).strip()
    if not text:
        return None, "no_field"
    now = now or datetime.now()
    local_tz = now.astimezone().tzinfo or timezone.utc

    relative_date = parse_relative_news_date(text, now)
    if relative_date:
        return relative_date, "ok"

    if text.isdigit() and len(text) in (10, 13):
        try:
            ts = int(text[:10]) if len(text) == 13 else int(text)
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(local_tz).date(), "ok"
        except (OSError, OverflowError, ValueError):
            pass

    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed_iso = datetime.fromisoformat(iso_candidate)
        if parsed_iso.tzinfo is not None:
            return parsed_iso.astimezone(local_tz).date(), "ok"
        return parsed_iso.date(), "ok"
    except ValueError:
        pass

    normalized = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text, flags=re.IGNORECASE)

    try:
        parsed_rfc = parsedate_to_datetime(normalized)
        if parsed_rfc:
            if parsed_rfc.tzinfo is not None:
                return parsed_rfc.astimezone(local_tz).date(), "ok"
            return parsed_rfc.date(), "ok"
    except (TypeError, ValueError):
        pass

    zh_match = re.search(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})\s*日?", text)
    if zh_match:
        try:
            return date(int(zh_match.group(1)), int(zh_match.group(2)), int(zh_match.group(3))), "ok"
        except ValueError:
            pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d",
        "%Y%m%d",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%a, %d %b %Y %H:%M:%S %z",
    ):
        try:
            parsed_dt = datetime.strptime(normalized, fmt)
            if parsed_dt.tzinfo is not None:
                return parsed_dt.astimezone(local_tz).date(), "ok"
            return parsed_dt.date(), "ok"
        except ValueError:
            continue

    return None, "parse_failed"


def normalize_news_publish_date(value: Any, now: Optional[datetime] = None) -> Optional[date]:
    """Normalize provider date value into a date object."""
    result, _ = normalize_news_publish_date_with_reason(value, now=now)
    return result



def contains_chinese_text(value: Optional[str]) -> bool:
    """Return True when the input contains CJK characters."""
    return bool(value and _CHINESE_TEXT_RE.search(value))



def is_us_stock(stock_code: str) -> bool:
    """判断是否为美股/美股指数代码。"""
    code = (stock_code or "").strip().upper()
    return bool(_US_STOCK_RE.match(code))



def is_foreign_stock(stock_code: str) -> bool:
    """判断是否为港股或美股"""
    code = (stock_code or "").strip()
    if _US_STOCK_RE.match(code):
        return True
    lower = code.lower()
    if lower.startswith("hk"):
        return True
    if code.isdigit() and len(code) == 5:
        return True
    return False



def should_prefer_chinese_news(
    stock_code: str,
    stock_name: str,
    focus_keywords: Optional[list[str]] = None,
) -> bool:
    """A 股或中文名称/关键词场景下优先中文资讯。"""
    if any(contains_chinese_text(keyword) for keyword in (focus_keywords or [])):
        return True
    if contains_chinese_text(stock_name):
        return True
    code = (stock_code or "").strip()
    return code.isdigit() and len(code) == 6



def is_chinese_news_result(item: Any) -> bool:
    """Heuristic check for Chinese-language news items."""
    return contains_chinese_text(" ".join(filter(None, [getattr(item, "title", None), getattr(item, "snippet", None), getattr(item, "source", None)])))



def prioritize_news_language(response: Any, *, prefer_chinese: bool) -> Tuple[Any, int]:
    """Reorder results by preferred language and return preferred-result count."""
    if not prefer_chinese or not getattr(response, "success", False) or not getattr(response, "results", None):
        return response, 0

    chinese_results = []
    other_results = []
    for item in response.results:
        if is_chinese_news_result(item):
            chinese_results.append(item)
        else:
            other_results.append(item)

    response_cls = response.__class__
    return (
        response_cls(
            query=response.query,
            results=chinese_results + other_results,
            provider=response.provider,
            success=response.success,
            error_message=response.error_message,
            search_time=response.search_time,
        ),
        len(chinese_results),
    )



def is_better_preferred_news_response(
    candidate: Any,
    *,
    candidate_preferred_count: int,
    best_response: Optional[Any],
    best_preferred_count: int,
) -> bool:
    """Prefer responses with more Chinese items, then more total items."""
    if best_response is None:
        return True
    if candidate_preferred_count != best_preferred_count:
        return candidate_preferred_count > best_preferred_count
    return len(candidate.results) > len(best_response.results)



def brave_search_locale(stock_code: str, *, prefer_chinese: bool) -> dict[str, str]:
    """Resolve Brave locale hints without forcing US bias onto non-US symbols."""
    if prefer_chinese:
        return {"search_lang": "zh-hans", "country": "CN"}
    if is_us_stock(stock_code):
        return {"search_lang": "en", "country": "US"}
    return {}



def is_index_or_etf(stock_code: str, stock_name: str) -> bool:
    """
    Judge if symbol is index-tracking ETF or market index.
    For such symbols, analysis focuses on index movement only, not issuer company risks.
    """
    code = (stock_code or '').strip().split('.')[0]
    if not code:
        return False
    if code.isdigit() and len(code) == 6 and code.startswith(_A_ETF_PREFIXES):
        return True
    if is_us_index_code(code):
        return True
    if is_foreign_stock(code):
        name_upper = (stock_name or '').upper()
        return any(kw in name_upper for kw in _ETF_NAME_KEYWORDS)
    return False
