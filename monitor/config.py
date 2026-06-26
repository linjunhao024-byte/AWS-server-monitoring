#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一配置管理模块

所有可配置项集中在此文件，通过菜单或手动编辑修改。
敏感信息默认为空，首次使用时通过菜单配置。
文件权限应设为 600。
"""

import os
import json

# ---------------------------------------------------------------------------
# 配置文件路径
# ---------------------------------------------------------------------------
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# ---------------------------------------------------------------------------
# 钉钉配置（默认为空，通过安装向导或菜单配置）
# ---------------------------------------------------------------------------
DINGTALK_WEBHOOK = ""
DINGTALK_SECRET = ""

# ---------------------------------------------------------------------------
# Telegram 配置（默认为空，通过安装向导或菜单配置）
# ---------------------------------------------------------------------------
TG_BOT_TOKEN = ""
TG_CHAT_ID = ""
TG_API_URL = "https://api.telegram.org"

# ---------------------------------------------------------------------------
# 讯飞星火大模型配置（默认为空，通过安装向导或菜单配置）
# ---------------------------------------------------------------------------
XFYUN_API_URL = "https://spark-api-open.xf-yun.com/v1/chat/completions"
XFYUN_API_KEY = ""
XFYUN_MODEL = "4.0Ultra"
XFYUN_ENABLED = False

# ---------------------------------------------------------------------------
# 邮件配置（默认为空，通过安装向导或菜单配置）
# ---------------------------------------------------------------------------
EMAIL_ENABLED = False
SMTP_SERVER = "smtp.exmail.qq.com"
SMTP_PORT = 465
SMTP_USE_SSL = True
SMTP_USERNAME = ""
SMTP_PASSWORD = ""
SENDER_NAME = "服务器监控"
EMAIL_RECIPIENTS = []
WEEKLY_REPORT_DAY = 6  # 周几发周报（0=周一，6=周日）

# ---------------------------------------------------------------------------
# 服务器配置
# ---------------------------------------------------------------------------
SERVER_ALIAS = "MAIN-LS-SERVER"
INTERFACE = "ens5"
DATA_DIR = "/var/log/bandwidth"
INSTALL_DIR = "/opt/bandwidth_monitor"

# ---------------------------------------------------------------------------
# Ping 配置
# ---------------------------------------------------------------------------
PING_GW = "169.254.169.254"       # AWS 网关
PING_EXT = "114.114.114.114"      # 公网目标
PING_COUNT = 1
PING_TIMEOUT = 1
PING_INTERVAL = 5  # 后台 ping 间隔（秒）

# ---------------------------------------------------------------------------
# 路由监测配置
# ---------------------------------------------------------------------------
ROUTE_TARGET = "114.114.114.114"
ROUTE_INTERVAL = 300  # 检测间隔（秒）

# ---------------------------------------------------------------------------
# 带宽分布分桶（单位 Mbps）
# ---------------------------------------------------------------------------
BUCKETS = [
    (0, 0.1),
    (0.1, 0.5),
    (0.5, 1.0),
    (1.0, 2.0),
    (2.0, 5.0),
    (5.0, 10.0),
    (10.0, 20.0),
    (20.0, float("inf")),
]

# ---------------------------------------------------------------------------
# 趋势图配置
# ---------------------------------------------------------------------------
CHART_SAVE_PATH = "/tmp/traffic_trend.png"

# ---------------------------------------------------------------------------
# 运维配置
# ---------------------------------------------------------------------------
LOG_RETENTION_DAYS = 30     # CSV 日志保留天数
DISK_ALERT_MB = 1024        # 数据目录磁盘告警阈值（MB）
CURRENT_VERSION = "3.2.3"   # 当前版本号

# ---------------------------------------------------------------------------
# 告警开关
# ---------------------------------------------------------------------------
ROUTE_ALERT_ENABLED = True  # 路由变化告警


# ---------------------------------------------------------------------------
# 配置持久化
# ---------------------------------------------------------------------------

# 需要持久化的字段列表
_PERSISTENT_KEYS = [
    "DINGTALK_WEBHOOK", "DINGTALK_SECRET",
    "TG_BOT_TOKEN", "TG_CHAT_ID",
    "XFYUN_API_KEY", "XFYUN_MODEL", "XFYUN_ENABLED",
    "EMAIL_ENABLED", "SMTP_SERVER", "SMTP_PORT", "SMTP_USE_SSL",
    "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_RECIPIENTS",
    "SERVER_ALIAS", "INTERFACE", "DATA_DIR",
    "ROUTE_TARGET", "ROUTE_INTERVAL",
    "ROUTE_ALERT_ENABLED", "LOG_RETENTION_DAYS", "DISK_ALERT_MB",
    "SENDER_NAME", "WEEKLY_REPORT_DAY",
]


def save_config():
    """将当前配置保存到 settings.json。"""
    data = {}
    module_globals = globals()
    for key in _PERSISTENT_KEYS:
        if key in module_globals:
            data[key] = module_globals[key]
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(CONFIG_FILE, 0o600)


def load_config():
    """从 settings.json 加载配置，覆盖默认值。"""
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        module_globals = globals()
        for key, value in data.items():
            if key in _PERSISTENT_KEYS and key in module_globals:
                module_globals[key] = value
        # 自动启用讯飞
        if module_globals.get("XFYUN_API_KEY"):
            module_globals["XFYUN_ENABLED"] = True
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] 加载配置失败: {e}")


# 启动时自动加载
load_config()
