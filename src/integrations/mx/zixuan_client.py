# -*- coding: utf-8 -*-
"""
妙想自选同步客户端。

职责：
- 读取 / 写入妙想自选池
- 暴露给正式主流程做同步落盘

说明：
- 这里先做正式目录内的独立封装，避免和 legacy 互相耦合
- 真实 API 形态以 mx-zixuan skill 为准，调用层应保持最小化
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import requests

from data_provider.base import canonical_stock_code
from src.config import get_config

logger = logging.getLogger(__name__)


def _resolve_skill_path() -> Path:
    """Resolve mx-zixuan skill directory from env or Hermes skill dir."""
    env_dir = (os.getenv('MX_ZIXUAN_SKILL_DIR') or os.getenv('MX_SKILL_DIR') or '').strip()
    if env_dir:
        return Path(env_dir).expanduser()
    return Path('/root/.hermes/skills/mx-zixuan')


def _clean_str(value: object) -> str:
    return str(value or '').strip()


class MxZixuanClient:
    """妙想自选客户端。"""

    RATE_LIMIT_CODE = 112
    RETRY_SLEEP_SECONDS = (2.0, 5.0, 10.0)
    BETWEEN_CODES_SLEEP_SECONDS = 0.5
    BETWEEN_VARIANTS_SLEEP_SECONDS = 0.3
    DEFAULT_API_ROOT = 'https://mkapi2.dfcfs.com/finskillshub/api/claw'
    QUERY_ENDPOINT = 'self-select/get'
    MANAGE_ENDPOINT = 'self-select/manage'

    @staticmethod
    def _code_variants(code: str) -> List[str]:
        """为自选同步生成更稳的代码尝试顺序。"""
        raw = str(code or '').strip().upper()
        if not raw:
            return []
        normalized = canonical_stock_code(raw)
        variants: List[str] = []
        for item in [raw, normalized]:
            item = str(item or '').strip().upper()
            if not item or item in variants:
                continue
            variants.append(item)
            base = item.split('.')[0]
            if base and base not in variants:
                variants.append(base)
        return variants

    def __init__(self, apikey: Optional[str] = None) -> None:
        self.apikey = (apikey or "").strip()
        if not self.apikey:
            raise ValueError("MX_APIKEY is required for MxZixuanClient")

        cfg = get_config()
        self.base_url = (
            _clean_str(getattr(cfg, 'mx_base_url', None)) or self.DEFAULT_API_ROOT
        ).rstrip('/')
        self.timeout = float(getattr(cfg, 'mx_timeout_seconds', 8.0) or 8.0)
        self._skill_imported = False
        self._skill = None

    def _load_skill(self):
        if self._skill_imported:
            return self._skill

        self._skill_imported = True
        try:
            import sys

            skill_path = _resolve_skill_path()
            if not skill_path.exists():
                raise FileNotFoundError(f"mx-zixuan skill path not found: {skill_path}")
            if str(skill_path) not in sys.path:
                sys.path.insert(0, str(skill_path))

            import mx_zixuan  # type: ignore

            self._skill = mx_zixuan
            return self._skill
        except Exception as exc:
            logger.warning("加载 mx-zixuan skill 失败: %s", exc)
            self._skill = None
            return None

    def _post(self, endpoint: str, payload: Dict[str, object]) -> Dict[str, object]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            'Content-Type': 'application/json',
            'apikey': self.apikey,
        }
        response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {'data': data}

    @staticmethod
    def _extract_codes(result: object) -> List[str]:
        if not isinstance(result, dict):
            return []

        queue: List[object] = [result]
        codes: List[str] = []
        seen = set()
        candidate_keys = ('SECURITY_CODE', 'securityCode', 'stockCode', 'code', 'Code')
        list_keys = ('dataList', 'list', 'items', 'rows', 'result', 'data', 'allResults')

        while queue:
            current = queue.pop(0)
            if isinstance(current, dict):
                for key in candidate_keys:
                    code = _clean_str(current.get(key))
                    if code and code not in seen:
                        seen.add(code)
                        codes.append(code)
                for key in list_keys:
                    value = current.get(key)
                    if isinstance(value, (dict, list)):
                        queue.append(value)
            elif isinstance(current, list):
                queue.extend(current)
        return codes

    def _query_self_select(self) -> Dict[str, object]:
        skill = self._load_skill()
        if skill is not None:
            return skill.query_self_select(self.apikey)
        return self._post(self.QUERY_ENDPOINT, {})

    def _manage_self_select(self, query: str) -> Dict[str, object]:
        skill = self._load_skill()
        if skill is not None:
            return skill.manage_self_select(self.apikey, query)
        return self._post(self.MANAGE_ENDPOINT, {'query': query})

    @staticmethod
    def _is_valid_sync_code(code: object) -> bool:
        raw = str(code or '').strip().upper()
        if not raw:
            return False
        if raw.startswith('HK'):
            return raw[2:].isdigit() and len(raw[2:]) == 5
        if '.' in raw:
            base, suffix = raw.rsplit('.', 1)
            if suffix in {'SH', 'SZ', 'BJ'}:
                raw = base
        return raw.isdigit() and len(raw) == 6

    def _normalize_sync_code(self, code: object) -> Optional[str]:
        raw = str(code or '').strip().upper()
        if not self._is_valid_sync_code(raw):
            return None
        normalized = canonical_stock_code(raw)
        return normalized if self._is_valid_sync_code(normalized) else None

    def list_codes(self) -> List[str]:
        try:
            result = self._query_self_select()
            raw_codes = self._extract_codes(result)
            normalized_codes: List[str] = []
            seen = set()
            invalid_codes: List[str] = []
            for code in raw_codes:
                normalized = self._normalize_sync_code(code)
                if not normalized:
                    invalid_codes.append(str(code))
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                normalized_codes.append(normalized)
            if invalid_codes:
                logger.warning("妙想自选列表存在异常代码，已过滤: %s", ', '.join(invalid_codes[:10]))
            return normalized_codes
        except Exception as exc:
            logger.warning("获取妙想自选列表失败: %s", exc)
            return []

    @staticmethod
    def _result_code(result: object) -> Optional[int]:
        if not isinstance(result, dict):
            return None
        for key in ("status", "code"):
            value = result.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    def _is_success(self, result: object) -> bool:
        code = self._result_code(result)
        return code == 0

    def _is_rate_limited(self, result: object, error: Optional[str] = None) -> bool:
        if self._result_code(result) == self.RATE_LIMIT_CODE:
            return True
        error_text = str(error or '')
        return 'Read timed out' in error_text or '请求频率过高' in error_text

    def _with_rate_limit_retry(self, action_desc: str, func):
        last_result = None
        last_error = None
        for attempt, retry_sleep in enumerate((0.0, *self.RETRY_SLEEP_SECONDS), start=1):
            if retry_sleep > 0:
                logger.warning("%s 命中频控，等待 %.1fs 后第 %d 次重试", action_desc, retry_sleep, attempt)
                time.sleep(retry_sleep)
            try:
                result = func()
                last_result = result
                if self._result_code(result) == self.RATE_LIMIT_CODE:
                    continue
                return result, last_error
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if "Read timed out" in last_error or "请求频率过高" in last_error:
                    continue
                return last_result, last_error
        return last_result, last_error

    def _manage_codes(self, codes: Sequence[str], action: str) -> Dict[str, object]:
        self._load_skill()

        done_key = "added" if action == "add" else "deleted"
        action_label = "添加" if action == "add" else "删除"
        action_template = "把{variant}添加到我的自选股列表" if action == "add" else "把{variant}从我的自选股列表删除"

        done: List[str] = []
        failed: List[str] = []
        fail_details: Dict[str, object] = {}
        abort_remaining = False
        for code_index, code in enumerate(codes):
            code = str(code).strip()
            normalized_code = self._normalize_sync_code(code)
            if not normalized_code:
                failed.append(code)
                fail_details[code] = {
                    "variants": [],
                    "last_result": None,
                    "last_error": 'invalid_code',
                }
                logger.warning("妙想自选%s跳过异常代码: %s", action_label, code)
                continue
            code = normalized_code
            if abort_remaining:
                failed.append(code)
                fail_details[code] = {
                    "variants": self._code_variants(code),
                    "last_result": None,
                    "last_error": 'skipped_after_rate_limit',
                }
                continue
            if code_index > 0:
                time.sleep(self.BETWEEN_CODES_SLEEP_SECONDS)
            variants = self._code_variants(code)
            last_result = None
            last_error = None
            ok = False
            hit_rate_limit = False
            for variant_index, variant in enumerate(variants):
                try:
                    if variant_index > 0:
                        time.sleep(self.BETWEEN_VARIANTS_SLEEP_SECONDS)
                    result, retry_error = self._with_rate_limit_retry(
                        f"妙想自选{action_label}: code={code} variant={variant}",
                        lambda variant=variant: self._manage_self_select(action_template.format(variant=variant)),
                    )
                    last_result = result
                    last_error = retry_error
                    ok = self._is_success(result)
                    hit_rate_limit = self._is_rate_limited(result, retry_error)
                    if ok:
                        done.append(code)
                        if variant != code:
                            logger.info("妙想自选%s成功（fallback）: %s -> %s", action_label, code, variant)
                        break
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    hit_rate_limit = self._is_rate_limited(None, last_error)
            if not ok:
                failed.append(code)
                fail_details[code] = {
                    "variants": variants,
                    "last_result": last_result,
                    "last_error": last_error,
                }
                if action == 'delete' and hit_rate_limit:
                    abort_remaining = True
                    logger.warning("妙想自选%s触发频控，停止后续删除以降低无效请求: code=%s", action_label, code)
                logger.warning("妙想自选%s失败: code=%s variants=%s last_error=%s last_result=%s", action_label, code, variants, last_error, last_result)

        return {
            "success": not failed,
            done_key: done,
            "failed": failed,
            "fail_details": fail_details,
        }

    def add_codes(self, codes: Sequence[str]) -> Dict[str, object]:
        return self._manage_codes(codes, action="add")

    def delete_codes(self, codes: Sequence[str]) -> Dict[str, object]:
        return self._manage_codes(codes, action="delete")
