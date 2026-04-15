# -*- coding: utf-8 -*-
"""妙想能力客户端封装。"""

import logging
import time
from typing import Any, Dict, Optional

import requests

from src.config import get_config
from .models import MxResponse

logger = logging.getLogger(__name__)


class MxClient:
    DEFAULT_API_ROOT = "https://mkapi2.dfcfs.com/finskillshub/api/claw"
    NEWS_SEARCH_ENDPOINT = "news-search"
    STOCK_SCREEN_ENDPOINT = "stock-screen"
    DATA_QUERY_ENDPOINT = "query"

    @staticmethod
    def _clean_str(value: Optional[Any]) -> Optional[str]:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return None

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: Optional[float] = None):
        cfg = get_config()
        cfg_base_url = self._clean_str(getattr(cfg, "mx_base_url", None))
        cfg_api_key = self._clean_str(getattr(cfg, "mx_apikey", None)) or self._clean_str(getattr(cfg, "mx_api_key", None))
        self.base_url = self._clean_str(base_url) or cfg_base_url or self.DEFAULT_API_ROOT
        self.api_key = self._clean_str(api_key) or cfg_api_key
        self.timeout = timeout or getattr(cfg, "mx_timeout_seconds", 8.0)
        self.enabled = bool(self.api_key)

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> MxResponse:
        if not self.enabled:
            return MxResponse(ok=False, error="mx_disabled")
        start = time.time()
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "Content-Type": "application/json",
            "apikey": self.api_key,
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            latency_ms = int((time.time() - start) * 1000)
            if resp.ok:
                try:
                    data = resp.json()
                except Exception:
                    data = resp.text
                return MxResponse(ok=True, data=data, source="mx", latency_ms=latency_ms, raw={"status_code": resp.status_code})
            return MxResponse(ok=False, error=f"http_{resp.status_code}", source="mx", latency_ms=latency_ms, raw={"text": resp.text[:500]})
        except requests.Timeout as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.info("mx request timed out after %.2fs: %s", self.timeout, exc)
            return MxResponse(ok=False, error=str(exc), source="mx", latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = int((time.time() - start) * 1000)
            logger.warning("mx request failed: %s", exc)
            return MxResponse(ok=False, error=str(exc), source="mx", latency_ms=latency_ms)

    def search(self, query: str, **kwargs: Any) -> MxResponse:
        # 统一妙想技能口径：
        # - 带 code/name 等股票筛选信息时，走 mx_xuangu 的 stock-screen
        # - 其他场景走 mx_search 的 news-search
        if any(k in kwargs for k in ("code", "name", "keyword", "stock_code", "stock_name")):
            payload = {"keyword": query}
            return self._post(self.STOCK_SCREEN_ENDPOINT, payload)
        payload = {"query": query, **kwargs}
        return self._post(self.NEWS_SEARCH_ENDPOINT, payload)

    def query(self, payload: Dict[str, Any]) -> MxResponse:
        return self._post(self.DATA_QUERY_ENDPOINT, payload)

    def healthcheck(self) -> MxResponse:
        return self._post(self.NEWS_SEARCH_ENDPOINT, {"query": "health"})
