# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class MxMoniClient:
    def __init__(self, apikey: Optional[str] = None) -> None:
        self.apikey = (apikey or "").strip()
        if not self.apikey:
            raise ValueError("MX_APIKEY is required for MxMoniClient")
        self._module = None

    def _load_module(self):
        if self._module is not None:
            return self._module
        skill_file = Path('/root/.openclaw/workspace/skills/mx-moni/mx_moni.py')
        if not skill_file.exists():
            raise FileNotFoundError(f"mx-moni skill not found: {skill_file}")
        spec = importlib.util.spec_from_file_location('mx_moni_skill', skill_file)
        if spec is None or spec.loader is None:
            raise ImportError('cannot load mx-moni skill module')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._module = mod
        return mod

    def _call_request(self, endpoint: str, body: Dict[str, Any], output_prefix: str) -> Dict[str, Any]:
        mod = self._load_module()
        old_apikey = os.environ.get('MX_APIKEY')
        os.environ['MX_APIKEY'] = self.apikey
        try:
            mod.MX_APIKEY = self.apikey
            mod.make_request(endpoint, body, output_prefix)
            output_path = Path(mod.OUTPUT_DIR) / f"{output_prefix}_raw.json"
            if not output_path.exists():
                raise FileNotFoundError(f"mx-moni output not found: {output_path}")
            return json.loads(output_path.read_text(encoding='utf-8'))
        finally:
            if old_apikey is None:
                os.environ.pop('MX_APIKEY', None)
            else:
                os.environ['MX_APIKEY'] = old_apikey

    def query_account(self) -> Dict[str, Any]:
        return self._call_request('/api/claw/mockTrading/balance', {'moneyUnit': 1}, 'mx_moni_query_account')

    def query_positions(self) -> Dict[str, Any]:
        return self._call_request('/api/claw/mockTrading/positions', {'moneyUnit': 1}, 'mx_moni_query_positions')

    def query_orders(self) -> Dict[str, Any]:
        return self._call_request('/api/claw/mockTrading/orders', {'fltOrderDrt': 0, 'fltOrderStatus': 0}, 'mx_moni_query_orders')

    def buy(self, stock_code: str, price: float, quantity: int, use_market_price: bool = False) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            'type': 'buy',
            'stockCode': stock_code,
            'quantity': quantity,
            'useMarketPrice': use_market_price,
        }
        if not use_market_price:
            body['price'] = price
        return self._call_request('/api/claw/mockTrading/trade', body, f'mx_moni_buy_{stock_code}')

    def sell(self, stock_code: str, price: float, quantity: int, use_market_price: bool = False) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            'type': 'sell',
            'stockCode': stock_code,
            'quantity': quantity,
            'useMarketPrice': use_market_price,
        }
        if not use_market_price:
            body['price'] = price
        return self._call_request('/api/claw/mockTrading/trade', body, f'mx_moni_sell_{stock_code}')
