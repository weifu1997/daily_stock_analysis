# -*- coding: utf-8 -*-
"""
妙想自选同步服务。

职责：
- 汇总正式主流程中的候选池与持仓池
- 与妙想自选池做差异比较
- 默认允许删除，确保自选池与正式主流程一致
- 输出可落地的同步摘要
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Sequence

from data_provider.base import canonical_stock_code

logger = logging.getLogger(__name__)


@dataclass
class ZixuanSyncResult:
    added: List[str]
    failed: List[str]
    deleted: List[str]
    skipped: List[str]
    current: List[str]
    target: List[str]
    fail_details: dict
    delete_fail_details: dict

    @property
    def summary(self) -> str:
        return (
            f"zixuan同步：新增 {len(self.added)}，删除 {len(self.deleted)}，"
            f"失败 {len(self.failed)}，跳过删除 {len(self.skipped)}"
        )

    @property
    def diff_summary(self) -> str:
        added_preview = ", ".join(self.added[:10]) if self.added else "无"
        deleted_preview = ", ".join(self.deleted[:10]) if self.deleted else "无"
        failed_preview = ", ".join(self.failed[:10]) if self.failed else "无"
        skipped_preview = ", ".join(self.skipped[:10]) if self.skipped else "无"
        current_preview = ", ".join(self.current[:10]) if self.current else "无"
        target_preview = ", ".join(self.target[:10]) if self.target else "无"
        detail_hint = ""
        if self.fail_details:
            sample_items = list(self.fail_details.items())[:3]
            sample_text = "; ".join(
                f"{code}: variants={info.get('variants')} err={info.get('last_error')} result={info.get('last_result')}"
                for code, info in sample_items
            )
            detail_hint = f"；失败详情[{sample_text}]"
        return (
            "zixuan同步明细："
            f"目标[{target_preview}]；"
            f"当前[{current_preview}]；"
            f"新增[{added_preview}]；"
            f"删除[{deleted_preview}]；"
            f"失败[{failed_preview}]；"
            f"跳过删除[{skipped_preview}]"
            f"{detail_hint}"
        )


class ZixuanSyncService:
    def __init__(self, client, allow_delete: bool = True) -> None:
        self.client = client
        self.allow_delete = allow_delete

    def _normalize_codes(self, codes: Sequence[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for code in codes:
            normalized = canonical_stock_code(str(code))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    def _build_target_codes(self, candidate_codes: Sequence[str], portfolio_codes: Sequence[str]) -> List[str]:
        merged: List[str] = []
        seen = set()
        for code in list(candidate_codes) + list(portfolio_codes):
            normalized = canonical_stock_code(str(code))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
        return merged

    def sync(self, candidate_codes: Sequence[str], portfolio_codes: Sequence[str]) -> ZixuanSyncResult:
        target_codes = self._build_target_codes(candidate_codes, portfolio_codes)
        current_codes = self._normalize_codes(self.client.list_codes())

        current_set = set(current_codes)
        target_set = set(target_codes)

        to_add = [code for code in target_codes if code not in current_set]
        to_delete = [code for code in current_codes if code not in target_set]

        add_result = self.client.add_codes(to_add)
        added = list(add_result.get("added", []))
        failed = list(add_result.get("failed", []))
        fail_details = dict(add_result.get("fail_details", {}) or {})

        deleted: List[str] = []
        skipped: List[str] = []
        delete_fail_details = {}
        if self.allow_delete and to_delete:
            del_result = self.client.delete_codes(to_delete)
            deleted = list(del_result.get("deleted", []))
            skipped = list(del_result.get("failed", []))
            delete_fail_details = dict(del_result.get("fail_details", {}) or {})
        else:
            skipped = list(to_delete)

        return ZixuanSyncResult(
            added=added,
            failed=failed,
            deleted=deleted,
            skipped=skipped,
            current=current_codes,
            target=target_codes,
            fail_details=fail_details,
            delete_fail_details=delete_fail_details,
        )
