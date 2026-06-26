#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
动态路由监测守护进程

从 route_monitor.py 迁移，使用共享模块。
定期运行 traceroute 检测路由路径变化。

用法：
  sudo python3 route_daemon.py [--target 114.114.114.114] [--interval 300]
"""

import os
import sys
import time
import argparse

from config import (
    ROUTE_TARGET, ROUTE_INTERVAL, DATA_DIR, ROUTE_ALERT_ENABLED,
)
from utils import now_iso, today_label, setup_signal_handler, is_running
from data_sources import run_traceroute, compare_routes
from notifications import send_dingtalk


# ---------------------------------------------------------------------------
# 日志写入
# ---------------------------------------------------------------------------

def log_route(output_dir: str, target: str, hops: list[str],
              change_info: dict | None):
    """将路由检测结果写入日志文件。"""
    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, f"route_log_{today_label()}.txt")

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n[{now_iso()}] target={target}\n")

        if change_info and change_info.get("changed"):
            f.write(f"  ⚠️  路由变化! 变化位置: {change_info['change_hop']}\n")
            f.write(f"  新增节点: {', '.join(change_info['added'])}\n")
            f.write(f"  消失节点: {', '.join(change_info['removed'])}\n")
            f.write(f"  跳数: {change_info['old_hops']} → {change_info['new_hops']}\n")
        else:
            f.write(f"  ✅ 路径无变化\n")

        f.write(f"  路径: {' → '.join(hops) if hops else 'traceroute 超时'}\n")

    return log_path


def build_route_alert(target: str, change_info: dict, hops: list[str]) -> str:
    """构建路由变化的钉钉告警消息。"""
    from notifications import _server_info_block
    path_str = " → ".join(hops[:5]) if hops else "超时"
    return (
        f"### ⚠️ 路由变化告警\n\n"
        f"{_server_info_block()}\n"
        f"- **目标**: {target}\n"
        f"- **时间**: {now_iso()}\n"
        f"- **变化位置**: {change_info['change_hop']}\n"
        f"- **新增节点**: {', '.join(change_info['added'])}\n"
        f"- **消失节点**: {', '.join(change_info['removed'])}\n"
        f"- **当前路径前5跳**: {path_str}\n\n"
        f"> 路由变化可能导致延迟波动和短暂卡顿"
    )


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def run_monitor(target: str, interval: int, output_dir: str):
    print(f"[{now_iso()}] 路由监测启动 | 目标={target} | 间隔={interval}秒 | 输出={output_dir}")

    # 钉钉启动通知
    try:
        from notifications import notify_service_start
        notify_service_start("路由监测", f"目标={target} 间隔={interval}s")
    except Exception:
        pass

    prev_hops = None
    change_count = 0

    while is_running():
        print(f"[{now_iso()}] 正在 traceroute {target} ...")
        hops = run_traceroute(target)

        if prev_hops is not None:
            diff = compare_routes(prev_hops, hops)
            if diff["changed"]:
                change_count += 1
                print(f"[{now_iso()}] ⚠️  路由变化! 变化位置: {diff['change_hop']}")
                log_route(output_dir, target, hops, diff)
                if ROUTE_ALERT_ENABLED:
                    alert = build_route_alert(target, diff, hops)
                    send_dingtalk("⚠️ 路由变化告警", alert)
                else:
                    print(f"[{now_iso()}] 路由告警已关闭，未推送钉钉")
            else:
                log_route(output_dir, target, hops, None)
                print(f"[{now_iso()}] ✅ 路径无变化 (累计变化 {change_count} 次)")
        else:
            log_route(output_dir, target, hops, None)
            print(f"[{now_iso()}] 📝 基线路径已记录 ({len(hops)} 跳)")

        prev_hops = hops

        for _ in range(interval):
            if not is_running():
                break
            time.sleep(1)

    print(f"[{now_iso()}] 路由监测已停止。累计检测到 {change_count} 次路由变化。")

    # 钉钉停止通知
    try:
        from notifications import notify_service_stop
        notify_service_stop("路由监测", f"累计检测到 {change_count} 次路由变化")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def main():
    setup_signal_handler()

    parser = argparse.ArgumentParser(description="动态路由监测守护进程")
    parser.add_argument(
        "-t", "--target", default=ROUTE_TARGET,
        help=f"traceroute 目标（默认 {ROUTE_TARGET}）",
    )
    parser.add_argument(
        "-n", "--interval", type=int, default=ROUTE_INTERVAL,
        help=f"检测间隔秒数（默认 {ROUTE_INTERVAL}）",
    )
    parser.add_argument(
        "-o", "--output", default=DATA_DIR,
        help=f"日志输出目录（默认 {DATA_DIR}）",
    )
    args = parser.parse_args()

    run_monitor(target=args.target, interval=args.interval, output_dir=args.output)


if __name__ == "__main__":
    main()
