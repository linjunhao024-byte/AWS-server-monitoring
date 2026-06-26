#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用工具函数模块

从各脚本中提取的纯函数，无外部依赖。
"""

import signal
import datetime

# ---------------------------------------------------------------------------
# 全局优雅退出标志
# ---------------------------------------------------------------------------
_running = True


def _signal_handler(signum, frame):
    """捕获 SIGTERM / SIGINT，设置退出标志。"""
    global _running
    _running = False


def setup_signal_handler():
    """注册信号处理器，用于 daemon 优雅退出。"""
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)


def is_running() -> bool:
    """检查是否应该继续运行。"""
    return _running


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """返回当前时间 ISO 格式字符串。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_label() -> str:
    """返回当天日期标签 YYYYMMDD。"""
    return datetime.datetime.now().strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 格式化工具
# ---------------------------------------------------------------------------

# 流量单位阈值
GB_THRESHOLD = 1024 * 1024 * 1024
MB_THRESHOLD = 1024 * 1024
KB_THRESHOLD = 1024

BYTES_PER_MBIT = 125000  # 1 Mbps = 125,000 Bytes/s


def format_bytes(bytes_value: int) -> str:
    """将字节数转换为人类可读的格式。"""
    if bytes_value >= GB_THRESHOLD:
        return f"{bytes_value / GB_THRESHOLD:.2f} GB"
    elif bytes_value >= MB_THRESHOLD:
        return f"{bytes_value / MB_THRESHOLD:.2f} MB"
    elif bytes_value >= KB_THRESHOLD:
        return f"{bytes_value / KB_THRESHOLD:.2f} KB"
    else:
        return f"{bytes_value} B"


def format_duration(seconds: int) -> str:
    """格式化时长。"""
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        return f"{seconds // 60}分{seconds % 60}秒"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}小时{m}分"


def bytes_to_mbps(byte_delta: int, interval: float) -> float:
    """将字节差值转换为 Mbps（精确到小数点后 4 位）。"""
    return round((byte_delta / BYTES_PER_MBIT) / interval, 4)


def calculate_growth_rate(current: float, previous: float) -> float | None:
    """计算增长率。"""
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100


def format_growth_rate(rate: float | None) -> str:
    """格式化增长率显示。"""
    if rate is None:
        return "N/A"
    if rate >= 0:
        return f"⬆️ +{rate:.1f}%"
    else:
        return f"⬇️ {rate:.1f}%"


def get_server_ip() -> str:
    """获取服务器 IP 地址（优先公网）。"""
    try:
        import requests
        response = requests.get("https://api.ipify.org", timeout=5)
        if response.status_code == 200:
            return response.text.strip()
    except Exception:
        pass

    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "未知IP"


def get_iso_week_info() -> dict:
    """获取 ISO 周数信息。"""
    today = datetime.datetime.now()
    iso_year, iso_week, iso_weekday = today.isocalendar()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)

    return {
        "year": iso_year,
        "week": iso_week,
        "weekday": iso_weekday,
        "week_start": monday,
        "week_end": sunday,
        "week_start_str": monday.strftime("%m-%d"),
        "week_end_str": sunday.strftime("%m-%d"),
        "days_elapsed": iso_weekday,
    }
