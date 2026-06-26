#!/usr/bin/env bash
# 全局快捷命令：monitor
# 用法: monitor [--check|--analyze|--status|--report|--rotate|--version|--update]
exec python3 /opt/bandwidth_monitor/main.py "$@"
