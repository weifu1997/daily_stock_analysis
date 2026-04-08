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
from typing import Dict, Iterable, List, Optional, Sequence

from data_provider.base import canonical_stock_code

logger = logging.getLogger(__name__)


class MxZixuanClient:
    """妙想自选客户端。"""

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
            from pathlib import Path

            skill_path = Path('/root/.openclaw/workspace/skills/mx-zixuan')
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

    def add_codes(self, codes: Sequence[str]) -> Dict[str, object]:
        skill = self._load_skill()
        if skill is None:
            return {"success": False, "added": [], "failed": list(codes), "message": "skill unavailable", "fail_details": {}}

        added: List[str] = []
        failed: List[str] = []
        fail_details: Dict[str, object] = {}
        for code in codes:
            code = str(code).strip()
            if not code:
                continue
            variants = self._code_variants(code)
            last_result = None
            last_error = None
            ok = False
            for variant in variants:
                try:
                    result = skill.manage_self_select(self.apikey, f"把{variant}添加到我的自选股列表")
                    last_result = result
                    ok = isinstance(result, dict) and (result.get('status') == 0 or result.get('code') == 0)
                    if ok:
                        added.append(code)
                        if variant != code:
                            logger.info("妙想自选添加成功（fallback）: %s -> %s", code, variant)
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
                logger.warning("妙想自选添加失败: code=%s variants=%s last_error=%s last_result=%s", code, variants, last_error, last_result)

        return {
            "success": not failed,
            "added": added,
            "failed": failed,
            "fail_details": fail_details,
        }

    def delete_codes(self, codes: Sequence[str]) -> Dict[str, object]:
        skill = self._load_skill()
        if skill is None:
            return {"success": False, "deleted": [], "failed": list(codes), "message": "skill unavailable", "fail_details": {}}

        deleted: List[str] = []
        failed: List[str] = []
        fail_details: Dict[str, object] = {}
        for code in codes:
            code = str(code).strip()
            if not code:
                continue
            variants = self._code_variants(code)
            last_result = None
            last_error = None
            ok = False
            for variant in variants:
                try:
                    result = skill.manage_self_select(self.apikey, f"把{variant}从我的自选股列表删除")
                    last_result = result
                    ok = isinstance(result, dict) and (result.get('status') == 0 or result.get('code') == 0)
                    if ok:
                        deleted.append(code)
                        if variant != code:
                            logger.info("妙想自选删除成功（fallback）: %s -> %s", code, variant)
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
                logger.warning("妙想自选删除失败: code=%s variants=%s last_error=%s last_result=%s", code, variants, last_error, last_result)

        return {
            "success": not failed,
            "deleted": deleted,
            "failed": failed,
            "fail_details": fail_details,
        }
