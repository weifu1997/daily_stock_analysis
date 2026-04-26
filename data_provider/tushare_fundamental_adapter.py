# -*- coding: utf-8 -*-
"""Tushare adapter for structured financial fundamentals."""

from __future__ import annotations

from threading import Semaphore
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

import logging

logger = logging.getLogger(__name__)

from .fundamental_adapter import _normalize_report_date, _safe_float, _safe_str

if TYPE_CHECKING:
    from .tushare_fetcher import TushareFetcher


_TUSHARE_FUNDAMENTAL_BUNDLE_GUARD = Semaphore(1)


class TushareFundamentalAdapter:
    """Build fundamental bundles from Tushare structured financial endpoints."""

    def __init__(self, fetcher: Optional["TushareFetcher"] = None):
        if fetcher is None:
            from .tushare_fetcher import TushareFetcher
            fetcher = TushareFetcher()
        self._fetcher = fetcher

    @staticmethod
    def _latest_row(df: Optional[pd.DataFrame]) -> Optional[pd.Series]:
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return None
        work_df = df.copy()
        sort_col = None
        for candidate in ("end_date", "ann_date", "f_ann_date"):
            if candidate in work_df.columns:
                sort_col = candidate
                break
        if sort_col is not None:
            work_df = work_df.sort_values(sort_col, ascending=False, na_position="last")
        return work_df.iloc[0]

    @staticmethod
    def _has_meaningful_payload(payload: Dict[str, Any]) -> bool:
        return any(value is not None and value != "" for value in payload.values())

    @staticmethod
    def _format_forecast_summary(row: pd.Series) -> str:
        end_date = _normalize_report_date(row.get("end_date")) or _safe_str(row.get("end_date"))
        forecast_type = _safe_str(row.get("type")) or "业绩预告"
        p_min = _safe_float(row.get("p_change_min"))
        p_max = _safe_float(row.get("p_change_max"))
        summary = _safe_str(row.get("summary"))
        parts = [part for part in [end_date, forecast_type] if part]
        if p_min is not None or p_max is not None:
            if p_min is not None and p_max is not None:
                parts.append(f"预计同比{p_min:.1f}%~{p_max:.1f}%")
            elif p_min is not None:
                parts.append(f"预计同比{p_min:.1f}%")
            elif p_max is not None:
                parts.append(f"预计同比{p_max:.1f}%")
        if summary:
            parts.append(summary)
        return " | ".join(parts)

    @staticmethod
    def _format_express_summary(row: pd.Series) -> str:
        end_date = _normalize_report_date(row.get("end_date")) or _safe_str(row.get("end_date"))
        revenue = _safe_float(row.get("revenue"))
        n_income = _safe_float(row.get("n_income"))
        diluted_eps = _safe_float(row.get("diluted_eps"))
        diluted_roe = _safe_float(row.get("diluted_roe"))
        parts = [part for part in [end_date, "业绩快报"] if part]
        if revenue is not None:
            parts.append(f"营收={revenue}")
        if n_income is not None:
            parts.append(f"净利润={n_income}")
        if diluted_eps is not None:
            parts.append(f"EPS={diluted_eps}")
        if diluted_roe is not None:
            parts.append(f"ROE={diluted_roe}")
        return " | ".join(parts)

    @staticmethod
    def _extract_disclosure_payload(row: pd.Series) -> Dict[str, Optional[str]]:
        return {
            "report_date": _normalize_report_date(row.get("end_date")),
            "pre_date": _normalize_report_date(row.get("pre_date")),
            "ann_date": _normalize_report_date(row.get("ann_date")),
            "actual_date": _normalize_report_date(row.get("actual_date")),
        }

    @staticmethod
    def _sum_hold_change(df: Optional[pd.DataFrame]) -> Optional[float]:
        if df is None or not isinstance(df, pd.DataFrame) or df.empty or "hold_change" not in df.columns:
            return None
        work_df = df.copy()
        if "end_date" in work_df.columns:
            latest_end = work_df["end_date"].astype(str).max()
            work_df = work_df[work_df["end_date"].astype(str) == latest_end]
        if "ann_date" in work_df.columns:
            latest_ann = work_df["ann_date"].astype(str).max()
            work_df = work_df[work_df["ann_date"].astype(str) == latest_ann]
        series = pd.to_numeric(work_df["hold_change"], errors="coerce").dropna()
        if series.empty:
            return None
        return float(series.sum())

    @staticmethod
    def _build_holdernumber_payload(df: Optional[pd.DataFrame]) -> Dict[str, Any]:
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return {}
        work_df = df.copy()
        for col in ("end_date", "ann_date"):
            if col in work_df.columns:
                work_df = work_df.sort_values(col, ascending=False, na_position="last")
                break
        latest = work_df.iloc[0]
        latest_num = _safe_float(latest.get("holder_num"))
        if latest_num is None:
            return {}
        payload: Dict[str, Any] = {
            "holder_num": int(latest_num),
            "holder_num_end_date": _normalize_report_date(latest.get("end_date")),
            "holder_num_ann_date": _normalize_report_date(latest.get("ann_date")),
        }
        if len(work_df) > 1:
            prev_num = _safe_float(work_df.iloc[1].get("holder_num"))
            if prev_num is not None:
                payload["holder_num_change"] = int(latest_num - prev_num)
        return payload

    def get_institution_data(self, stock_code: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "status": "not_supported",
            "institution": {},
            "source_chain": [],
            "errors": [],
        }
        if not self._fetcher.is_available():
            result["errors"].append("tushare_unavailable")
            return result

        top10_df = None
        top10_float_df = None
        holdernumber_df = None
        try:
            top10_df = self._fetcher.get_top10_holders_df(stock_code)
        except Exception as exc:
            logger.warning(f"Broad exception caught: {exc}", exc_info=True)
            result["errors"].append(f"top10_holders:{type(exc).__name__}")
        try:
            top10_float_df = self._fetcher.get_top10_floatholders_df(stock_code)
        except Exception as exc:
            logger.warning(f"Broad exception caught: {exc}", exc_info=True)
            result["errors"].append(f"top10_floatholders:{type(exc).__name__}")
        try:
            holdernumber_df = self._fetcher.get_stk_holdernumber_df(stock_code)
        except Exception as exc:
            logger.warning(f"Broad exception caught: {exc}", exc_info=True)
            result["errors"].append(f"stk_holdernumber:{type(exc).__name__}")

        top10_change = self._sum_hold_change(top10_df)
        if top10_change is not None:
            result["institution"]["top10_holder_change"] = top10_change
            result["source_chain"].append("top10_holders:tushare_top10_holders")

        top10_float_change = self._sum_hold_change(top10_float_df)
        if top10_float_change is not None:
            result["institution"]["top10_float_holder_change"] = top10_float_change
            result["source_chain"].append("top10_floatholders:tushare_top10_floatholders")

        holdernumber_payload = self._build_holdernumber_payload(holdernumber_df)
        if holdernumber_payload:
            result["institution"].update(holdernumber_payload)
            result["source_chain"].append("holder_num:tushare_stk_holdernumber")

        if result["institution"]:
            result["status"] = "ok"
        elif result["errors"]:
            result["status"] = "partial"
        return result

    def get_fundamental_bundle(self, stock_code: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }
        if not self._fetcher.is_available():
            result["errors"].append("tushare_unavailable")
            return result

        with _TUSHARE_FUNDAMENTAL_BUNDLE_GUARD:
            income_row = self._latest_row(self._fetcher.get_income_df(stock_code))
            fina_row = self._latest_row(self._fetcher.get_fina_indicator_df(stock_code))
            cashflow_row = self._latest_row(self._fetcher.get_cashflow_df(stock_code))
            forecast_row = self._latest_row(self._fetcher.get_forecast_df(stock_code))
            express_row = self._latest_row(self._fetcher.get_express_df(stock_code))
            disclosure_row = self._latest_row(self._fetcher.get_disclosure_date_df(stock_code))

        financial_report = {
            "report_date": None,
            "revenue": None,
            "total_revenue": None,
            "net_profit_parent": None,
            "net_income": None,
            "basic_eps": None,
            "operate_profit": None,
            "total_profit": None,
            "rd_exp": None,
            "operating_cash_flow": None,
            "roe": None,
            "oper_cost": None,
            "sell_exp": None,
            "admin_exp": None,
            "fin_exp": None,
            "income_tax": None,
            "invest_income": None,
            "ebit": None,
            "ebitda": None,
        }
        financial_summary = {
            "report_date": None,
            "total_revenue": None,
            "n_income_attr_p": None,
            "n_income": None,
            "basic_eps": None,
            "operate_profit": None,
            "total_profit": None,
            "rd_exp": None,
            "revenue_yoy": None,
            "profit_yoy": None,
            "roe": None,
            "gross_margin": None,
            "netprofit_margin": None,
            "debt_to_assets": None,
            "roa": None,
        }

        if income_row is not None:
            report_date = _normalize_report_date(income_row.get("end_date"))
            financial_report.update(
                {
                    "report_date": report_date,
                    "revenue": _safe_float(income_row.get("revenue")),
                    "total_revenue": _safe_float(income_row.get("total_revenue")),
                    "net_profit_parent": _safe_float(income_row.get("n_income_attr_p")),
                    "net_income": _safe_float(income_row.get("n_income")),
                    "basic_eps": _safe_float(income_row.get("basic_eps")),
                    "operate_profit": _safe_float(income_row.get("operate_profit")),
                    "total_profit": _safe_float(income_row.get("total_profit")),
                    "rd_exp": _safe_float(income_row.get("rd_exp")),
                    "oper_cost": _safe_float(income_row.get("oper_cost")),
                    "sell_exp": _safe_float(income_row.get("sell_exp")),
                    "admin_exp": _safe_float(income_row.get("admin_exp")),
                    "fin_exp": _safe_float(income_row.get("fin_exp")),
                    "income_tax": _safe_float(income_row.get("income_tax")),
                    "invest_income": _safe_float(income_row.get("invest_income")),
                    "ebit": _safe_float(income_row.get("ebit")),
                    "ebitda": _safe_float(income_row.get("ebitda")),
                }
            )
            financial_summary.update(
                {
                    "report_date": report_date,
                    "total_revenue": _safe_float(income_row.get("total_revenue")),
                    "n_income_attr_p": _safe_float(income_row.get("n_income_attr_p")),
                    "n_income": _safe_float(income_row.get("n_income")),
                    "basic_eps": _safe_float(income_row.get("basic_eps")),
                    "operate_profit": _safe_float(income_row.get("operate_profit")),
                    "total_profit": _safe_float(income_row.get("total_profit")),
                    "rd_exp": _safe_float(income_row.get("rd_exp")),
                }
            )
            result["source_chain"].append("financial_report:tushare_income")

        if fina_row is not None:
            revenue_yoy = _safe_float(fina_row.get("tr_yoy"))
            profit_yoy = _safe_float(fina_row.get("netprofit_yoy"))
            roe = _safe_float(fina_row.get("roe"))
            gross_margin = _safe_float(fina_row.get("grossprofit_margin") or fina_row.get("gross_margin"))
            result["growth"] = {
                "revenue_yoy": revenue_yoy,
                "net_profit_yoy": profit_yoy,
                "roe": roe,
                "gross_margin": gross_margin,
            }
            financial_report["roe"] = roe
            financial_summary["revenue_yoy"] = revenue_yoy
            financial_summary["profit_yoy"] = profit_yoy
            financial_summary["roe"] = roe
            financial_summary["gross_margin"] = gross_margin
            financial_summary["netprofit_margin"] = _safe_float(fina_row.get("netprofit_margin"))
            financial_summary["debt_to_assets"] = _safe_float(fina_row.get("debt_to_assets"))
            financial_summary["roa"] = _safe_float(fina_row.get("roa"))
            if not financial_report.get("report_date"):
                financial_report["report_date"] = _normalize_report_date(fina_row.get("end_date"))
            if not financial_summary.get("report_date"):
                financial_summary["report_date"] = _normalize_report_date(fina_row.get("end_date"))
            result["source_chain"].append("financial_metrics:tushare_fina_indicator")

        if cashflow_row is not None:
            financial_report["operating_cash_flow"] = _safe_float(cashflow_row.get("n_cashflow_act"))
            financial_report["n_cashflow_inv_act"] = _safe_float(cashflow_row.get("n_cashflow_inv_act"))
            financial_report["n_cash_flows_fnc_act"] = _safe_float(cashflow_row.get("n_cash_flows_fnc_act"))
            financial_report["c_cash_equ_end_period"] = _safe_float(cashflow_row.get("c_cash_equ_end_period"))
            result["source_chain"].append("financial_cashflow:tushare_cashflow")

        if self._has_meaningful_payload(financial_report):
            result["earnings"]["financial_report"] = financial_report
        if self._has_meaningful_payload(financial_summary):
            result["earnings"]["financial_summary"] = financial_summary

        if forecast_row is not None:
            summary = self._format_forecast_summary(forecast_row)
            if summary:
                result["earnings"]["forecast_summary"] = summary
                result["source_chain"].append("earnings_forecast:tushare_forecast")

        if express_row is not None:
            summary = self._format_express_summary(express_row)
            if summary:
                result["earnings"]["quick_report_summary"] = summary
                result["source_chain"].append("earnings_quick:tushare_express")

        if disclosure_row is not None:
            disclosure_payload = self._extract_disclosure_payload(disclosure_row)
            if self._has_meaningful_payload(disclosure_payload):
                result["earnings"]["disclosure_date"] = disclosure_payload
                result["source_chain"].append("disclosure_date:tushare_disclosure_date")

        has_content = bool(result["growth"] or result["earnings"] or result["institution"])
        result["status"] = "partial" if has_content else "not_supported"
        return result
