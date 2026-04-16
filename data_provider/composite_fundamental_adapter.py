# -*- coding: utf-8 -*-
"""Composite fundamental adapter that merges structured Tushare data with AkShare fallbacks."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _merge_scalar_dict(primary: Optional[Dict[str, Any]], secondary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if isinstance(primary, dict):
        result.update(primary)
    if isinstance(secondary, dict):
        for key, value in secondary.items():
            if key not in result or result[key] in (None, "", [], {}):
                result[key] = value
    return result


class CompositeFundamentalAdapter:
    def __init__(self, primary: Any, secondary: Any, merge_secondary_bundle: bool = True):
        self._primary = primary
        self._secondary = secondary
        self._merge_secondary_bundle = merge_secondary_bundle

    def get_capital_flow(self, stock_code: str, top_n: int = 5) -> Dict[str, Any]:
        if self._secondary and hasattr(self._secondary, "get_capital_flow"):
            return self._secondary.get_capital_flow(stock_code, top_n=top_n)
        return {
            "status": "not_supported",
            "stock_flow": {},
            "sector_rankings": {"top": [], "bottom": []},
            "source_chain": [],
            "errors": ["capital_flow_provider_unavailable"],
        }

    def get_institution_data(self, stock_code: str) -> Dict[str, Any]:
        primary_result = self._primary.get_institution_data(stock_code) if self._primary and hasattr(self._primary, "get_institution_data") else {}
        secondary_result = self._secondary.get_institution_data(stock_code) if self._secondary and hasattr(self._secondary, "get_institution_data") else {}

        primary_institution = primary_result.get("institution", {}) if isinstance(primary_result, dict) else {}
        secondary_institution = secondary_result.get("institution", {}) if isinstance(secondary_result, dict) else {}
        merged_institution = _merge_scalar_dict(primary_institution, secondary_institution)

        source_chain: List[Any] = []
        for chain in (
            primary_result.get("source_chain", []) if isinstance(primary_result, dict) else [],
            secondary_result.get("source_chain", []) if isinstance(secondary_result, dict) else [],
        ):
            for item in chain:
                if item not in source_chain:
                    source_chain.append(item)

        errors: List[Any] = []
        for err_list in (
            primary_result.get("errors", []) if isinstance(primary_result, dict) else [],
            secondary_result.get("errors", []) if isinstance(secondary_result, dict) else [],
        ):
            for item in err_list:
                if item not in errors:
                    errors.append(item)

        has_content = bool(merged_institution)
        errors = errors or (["institution_provider_unavailable"] if not (self._primary or self._secondary) else [])
        return {
            "status": self._merge_status(
                primary_result.get("status") if isinstance(primary_result, dict) else None,
                secondary_result.get("status") if isinstance(secondary_result, dict) else None,
                has_content,
            ),
            "institution": merged_institution,
            "source_chain": source_chain,
            "errors": errors,
        }

    def get_dragon_tiger_flag(self, stock_code: str, lookback_days: int = 20) -> Dict[str, Any]:
        if self._secondary and hasattr(self._secondary, "get_dragon_tiger_flag"):
            return self._secondary.get_dragon_tiger_flag(stock_code, lookback_days=lookback_days)
        return {
            "status": "not_supported",
            "is_on_list": False,
            "recent_count": 0,
            "latest_date": None,
            "source_chain": [],
            "errors": ["dragon_tiger_provider_unavailable"],
        }

    @staticmethod
    def _status_rank(status: Optional[str]) -> int:
        return {"failed": 0, "not_supported": 1, "partial": 2, "ok": 3}.get(str(status or "not_supported"), 1)

    @classmethod
    def _merge_status(cls, primary_status: Optional[str], secondary_status: Optional[str], has_content: bool) -> str:
        if not has_content:
            return "not_supported"
        best = primary_status if cls._status_rank(primary_status) >= cls._status_rank(secondary_status) else secondary_status
        best = str(best or "partial")
        return "partial" if best == "not_supported" else best

    def get_fundamental_bundle(self, stock_code: str) -> Dict[str, Any]:
        primary_result = self._primary.get_fundamental_bundle(stock_code) if self._primary else {}
        secondary_result = {}
        if self._merge_secondary_bundle and self._secondary and hasattr(self._secondary, "get_fundamental_bundle"):
            secondary_result = self._secondary.get_fundamental_bundle(stock_code)

        primary_growth = primary_result.get("growth", {}) if isinstance(primary_result, dict) else {}
        primary_earnings = primary_result.get("earnings", {}) if isinstance(primary_result, dict) else {}
        primary_institution = primary_result.get("institution", {}) if isinstance(primary_result, dict) else {}
        secondary_growth = secondary_result.get("growth", {}) if isinstance(secondary_result, dict) else {}
        secondary_earnings = secondary_result.get("earnings", {}) if isinstance(secondary_result, dict) else {}
        secondary_institution = secondary_result.get("institution", {}) if isinstance(secondary_result, dict) else {}

        merged_growth = _merge_scalar_dict(primary_growth, secondary_growth)
        merged_institution = _merge_scalar_dict(primary_institution, secondary_institution)
        merged_earnings: Dict[str, Any] = {}
        for key in set(secondary_earnings.keys()) | set(primary_earnings.keys()):
            p_val = primary_earnings.get(key)
            s_val = secondary_earnings.get(key)
            if isinstance(p_val, dict) or isinstance(s_val, dict):
                merged = _merge_scalar_dict(p_val if isinstance(p_val, dict) else {}, s_val if isinstance(s_val, dict) else {})
                if merged:
                    merged_earnings[key] = merged
                continue
            chosen = p_val if p_val not in (None, "", [], {}) else s_val
            if chosen not in (None, "", [], {}):
                merged_earnings[key] = chosen

        source_chain: List[Any] = []
        for chain in (primary_result.get("source_chain", []) if isinstance(primary_result, dict) else [],
                      secondary_result.get("source_chain", []) if isinstance(secondary_result, dict) else []):
            for item in chain:
                if item not in source_chain:
                    source_chain.append(item)

        errors: List[Any] = []
        for err_list in (primary_result.get("errors", []) if isinstance(primary_result, dict) else [],
                         secondary_result.get("errors", []) if isinstance(secondary_result, dict) else []):
            for item in err_list:
                if item not in errors:
                    errors.append(item)

        has_content = bool(merged_growth or merged_earnings or merged_institution)
        return {
            "status": self._merge_status(
                primary_result.get("status") if isinstance(primary_result, dict) else None,
                secondary_result.get("status") if isinstance(secondary_result, dict) else None,
                has_content,
            ),
            "growth": merged_growth,
            "earnings": merged_earnings,
            "institution": merged_institution,
            "source_chain": source_chain,
            "errors": errors,
        }
