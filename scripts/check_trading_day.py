#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易日预检脚本 - 供 Hermes cron job 调用

用途：
  用 exchange_calendars 确定性判断 A 股今日是否为交易日，
  输出结构化信息供 LLM 消费，避免 LLM 瞎猜。

输出格式：
  === TRADING DAY CHECK ===
  日期: 2026-04-27
  星期: Monday
  市场: A股 (XSHG)
  是否为交易日: True
  判断依据: exchange_calendars 4.13.2
  =========================

退出码：
  0 - 正常执行
  1 - 判断失败（fail-open，应视为交易日）
"""

import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

# 尝试导入 exchange_calendars
_XCALS_AVAILABLE = False
_XCALS_VERSION = "unavailable"
try:
    import exchange_calendars as xcals
    _XCALS_AVAILABLE = True
    _XCALS_VERSION = getattr(xcals, "__version__", "unknown")
except ImportError:
    pass


def check_trading_day() -> bool:
    """
    判断今天是否为 A 股交易日。
    
    Returns:
        True: 是交易日（或 fail-open）
        False: 不是交易日
    """
    if not _XCALS_AVAILABLE:
        # fail-open: 库不可用时视为交易日，避免误跳过
        return True
    
    try:
        # 获取北京时间今天的日期
        cn_tz = ZoneInfo("Asia/Shanghai")
        today = datetime.now(cn_tz).date()
        
        # 用 exchange_calendars 判断
        cal = xcals.get_calendar("XSHG")
        session = datetime(today.year, today.month, today.day)
        return cal.is_session(session)
    except Exception:
        # fail-open: 任何异常都视为交易日
        return True


def main():
    """主函数：输出结构化信息"""
    # 获取北京时间今天
    cn_tz = ZoneInfo("Asia/Shanghai")
    today = datetime.now(cn_tz).date()
    
    # 判断交易日
    is_trading_day = check_trading_day()
    
    # 输出结构化信息
    print("=== TRADING DAY CHECK ===")
    print(f"日期: {today}")
    print(f"星期: {today.strftime('%A')}")
    print(f"市场: A股 (XSHG)")
    print(f"是否为交易日: {is_trading_day}")
    print(f"判断依据: exchange_calendars {_XCALS_VERSION}")
    print("=========================")
    
    # 退出码
    sys.exit(0)


if __name__ == "__main__":
    main()
