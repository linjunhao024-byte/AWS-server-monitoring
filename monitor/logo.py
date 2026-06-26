#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LIN-Monitor 品牌标识

所有 Logo 设计均为纯文本，不依赖终端宽度或字体。
"""

# 主 Logo（框线内显示，固定宽度）
LOGO_TEXT = "LIN-Monitor"

# 完整名称
LOGO_FULL = "LIN-Server-Monitor"

# 副标题
LOGO_TAGLINE = "Advanced Cloud State Guardian"

# 版本行（菜单标题用）
LOGO_VERSION_LINE = "Advanced Cloud State Guardian"

# 关于页专用 Logo（纯 ASCII，抗变形）
LOGO_ABOUT = """\
  +=======================================+
  |                                       |
  |    _     _   __  __                   |
  |   | |   | | |  \\/  |                  |
  |   | |   | | | |\\/| |                  |
  |   | |___| | | |  | |                  |
  |   |_______| |_|  |_|                  |
  |                                       |
  |        M O N I T O R                  |
  |                                       |
  +=======================================+"""

# 通知签名
SIGNATURE = "— LIN-Monitor"
SIGNATURE_TG = "_Powered by LIN-Monitor_"

# 关于信息
ABOUT_INFO = {
    "name": "LIN-Monitor",
    "full_name": "LIN-Server-Monitor",
    "author": "LIN",
    "github": "https://github.com/linjunhao024-byte/AWS-server-monitoring",
    "description": "AWS Lightsail 服务器监控 · 带宽积分反推 · 路由拓扑感知 · 智能告警",
    "features": [
        "1Hz 带宽采样",
        "带宽积分机制反推",
        "动态路由监测",
        "钉钉 / Telegram / 邮件多通道推送",
        "讯飞星火 AI 深度分析",
        "交互式终端菜单",
        "Telegram 内联键盘远程操控",
    ],
    "tech": [
        ("语言", "Python 3.10+ (零依赖核心)"),
        ("平台", "AWS Lightsail / Debian"),
        ("服务", "systemd (daemon + timer)"),
        ("通知", "钉钉 · Telegram · 邮件"),
        ("AI", "讯飞星火 4.0 Ultra"),
    ],
}
