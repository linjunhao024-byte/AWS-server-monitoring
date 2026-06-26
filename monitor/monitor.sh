#!/usr/bin/env bash
# LIN-Monitor — 全局快捷命令
# 用法: monitor [--check|--analyze|--status|--report|--rotate|--version|--update]
exec python3 /opt/bandwidth_monitor/main.py "$@"
