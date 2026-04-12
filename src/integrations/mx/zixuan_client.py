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

from data_provider.base import canonical_stock_code

logger = logging.getLogger(__name__)


def _resolve_skill_path() -> Path:
    """Resolve mx-zixuan skill directory from env or legacy default path."""
    env_dir = (os.getenv('MX_ZIXUAN_SKILL_DIR') or os.getenv('MX_SKILL_DIR') or '').strip()
    if env_dir:
        return Path(env_dir).expanduser()
    return Path('/root/.openclaw/workspace/skills/mx-zixuan')


class MxZixuanClient:
    """妙想自选客户端。"""

    RATE_LIMIT_CODE = 112
    RETRY_SLEEP_SECONDS = (2.0, 5.0, 10.0)
    BETWEEN_CODES_SLEEP_SECONDS = 0.5
    BETWEEN_VARIANTS_SLEEP_SECONDS = 0.3

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
    def list_codes(self) -> List[str]:
        skill = self._load_skill()
        if skill is None:
            return []
        try:
            result = skill.query_self_select(self.apikey)
            data = result.get('data', {}) if isinstance(result, dict) else {}
            all_results = data.get('allResults', {}) if isinstance(data, dict) else {}
            result_data = all_results.get('result', {}) if isinstance(all_results, dict) else {}
            data_list = result_data.get('dataList', []) if isinstance(result_data, dict) else []
            codes: List[str] = []
            for row in data_list or []:
                code = str(row.get('SECURITY_CODE') or row.get('code') or '').strip()
                if code:
                    codes.append(code)
            return codes
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
        skill = self._load_skill()
        if skill is None:
            empty_key = "added" if action == "add" else "deleted"
            return {"success": False, empty_key: [], "failed": list(codes), "message": "skill unavailable", "fail_details": {}}

        done_key = "added" if action == "add" else "deleted"
        action_label = "添加" if action == "add" else "删除"
        action_template = "把{variant}添加到我的自选股列表" if action == "add" else "把{variant}从我的自选股列表删除"

        done: List[str] = []
        failed: List[str] = []
        fail_details: Dict[str, object] = {}
        for code_index, code in enumerate(codes):
            code = str(code).strip()
            if not code:
                continue
            if code_index > 0:
                time.sleep(self.BETWEEN_CODES_SLEEP_SECONDS)
            variants = self._code_variants(code)
            last_result = None
            last_error = None
            ok = False
            for variant_index, variant in enumerate(variants):
                try:
                    if variant_index > 0:
                        time.sleep(self.BETWEEN_VARIANTS_SLEEP_SECONDS)
                    result, retry_error = self._with_rate_limit_retry(
                        f"妙想自选{action_label}: code={code} variant={variant}",
                        lambda variant=variant: skill.manage_self_select(self.apikey, action_template.format(variant=variant)),
                    )
                    last_result = result
                    last_error = retry_error
                    ok = self._is_success(result)
                    if ok:
                        done.append(code)
                        if variant != code:
                            logger.info("妙想自选%s成功（fallback）: %s -> %s", action_label, code, variant)
                        break
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
            if not ok:
                failed.append(code)
                fail_details[code] = {
                    "variants": variants,
                    "last_result": last_result,
                    "last_error": last_error,
                }
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
