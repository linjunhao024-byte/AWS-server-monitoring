#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AWS 网卡带宽 1Hz 采集守护进程

从 bandwidth_monitor.py 迁移，使用共享模块。
每秒无条件写入一行：[timestamp, rx_mbps, tx_mbps, cpu_load_1m, rtt_*, loss_*]。

用法：
  sudo python3 monitor_daemon.py [--interface ens5] [--output /var/log/bandwidth]
"""

import os
import sys
import csv
import time
import argparse
import datetime
import threading

from config import (
    INTERFACE, DATA_DIR, PING_GW, PING_EXT, PING_INTERVAL,
)
from utils import (
    now_iso, today_label, bytes_to_mbps,
    setup_signal_handler, is_running,
)
from data_sources import read_counter, read_loadavg, ping_once, check_interface


# ---------------------------------------------------------------------------
# 全局 Ping 缓存
# ---------------------------------------------------------------------------
_ping_cache = {
    "rtt_gw": -1.0,
    "loss_gw": 100.0,
    "rtt_ext": -1.0,
    "loss_ext": 100.0,
}
_ping_lock = threading.Lock()


def _ping_background():
    """后台 ping 线程：每 PING_INTERVAL 秒更新一次缓存。"""
    while is_running():
        loss_gw, rtt_gw = ping_once(PING_GW)
        loss_ext, rtt_ext = ping_once(PING_EXT)
        with _ping_lock:
            _ping_cache["rtt_gw"] = rtt_gw
            _ping_cache["loss_gw"] = loss_gw
            _ping_cache["rtt_ext"] = rtt_ext
            _ping_cache["loss_ext"] = loss_ext
        time.sleep(max(0, PING_INTERVAL - 4))


# ---------------------------------------------------------------------------
# CSV 写入器
# ---------------------------------------------------------------------------

class DailyCSVWriter:
    """管理当天的 CSV 日志文件。"""

    HEADER = [
        "timestamp", "rx_mbps", "tx_mbps", "cpu_load_1m",
        "rtt_gw_ms", "rtt_ext_ms", "loss_gw_pct", "loss_ext_pct",
    ]

    def __init__(self, output_dir: str, interface: str):
        self._output_dir = output_dir
        self._interface = interface
        self._date_label = today_label()
        self._file = None
        self._writer = None
        self._peak_tx = {"value": 0.0, "time": "N/A"}
        self._peak_rx = {"value": 0.0, "time": "N/A"}
        self._total_records = 0
        self._open_new_file()

    def _file_path(self) -> str:
        return os.path.join(
            self._output_dir,
            f"traffic_log_{self._date_label}_{self._interface}.csv",
        )

    def _open_new_file(self):
        os.makedirs(self._output_dir, exist_ok=True)
        path = self._file_path()
        file_exists = os.path.exists(path)
        self._file = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.HEADER)
        if not file_exists:
            self._writer.writeheader()
            self._file.flush()
        self._peak_tx = {"value": 0.0, "time": "N/A"}
        self._peak_rx = {"value": 0.0, "time": "N/A"}
        self._total_records = 0

    def write_row(self, row: dict):
        """写入一行数据，同时更新峰值追踪。"""
        self._writer.writerow(row)
        self._file.flush()
        self._total_records += 1

        if row["tx_mbps"] > self._peak_tx["value"]:
            self._peak_tx = {"value": row["tx_mbps"], "time": row["timestamp"]}
        if row["rx_mbps"] > self._peak_rx["value"]:
            self._peak_rx = {"value": row["rx_mbps"], "time": row["timestamp"]}

    def flush_and_rotate(self) -> str:
        """写入峰值摘要 → 关闭文件 → 返回文件路径。"""
        path = self._file_path()
        self._file.write("\n# ===== DAILY SUMMARY =====\n")
        self._file.write(f"# date: {self._date_label}\n")
        self._file.write(f"# interface: {self._interface}\n")
        self._file.write(f"# total_records: {self._total_records}\n")
        self._file.write(f"# peak_tx_mbps: {self._peak_tx['value']}\n")
        self._file.write(f"# peak_tx_time: {self._peak_tx['time']}\n")
        self._file.write(f"# peak_rx_mbps: {self._peak_rx['value']}\n")
        self._file.write(f"# peak_rx_time: {self._peak_rx['time']}\n")
        self._file.write("# ===== END SUMMARY =====\n")
        self._file.close()
        self._file = None
        self._writer = None
        return path

    def rotate_for_new_day(self):
        """日期变更后切换到新文件。"""
        if self._file:
            self.flush_and_rotate()
        self._date_label = today_label()
        self._open_new_file()

    def close(self):
        """关闭文件。"""
        if self._file:
            try:
                self.flush_and_rotate()
            except Exception:
                self._file.close()


# ---------------------------------------------------------------------------
# 日切检查
# ---------------------------------------------------------------------------

DAILY_CUT_HOUR = 23
DAILY_CUT_MINUTE = 59


def is_daily_cut_time() -> bool:
    """判断当前时刻是否到达日切时间 23:59。"""
    now = datetime.datetime.now()
    return now.hour == DAILY_CUT_HOUR and now.minute == DAILY_CUT_MINUTE


# ---------------------------------------------------------------------------
# 主监控循环
# ---------------------------------------------------------------------------

def run_monitor(interface: str, output_dir: str):
    """严格 1Hz 采样循环。"""
    writer = DailyCSVWriter(output_dir, interface)

    prev_rx = read_counter(interface, "rx_bytes")
    prev_tx = read_counter(interface, "tx_bytes")
    prev_time = time.monotonic()

    # 启动后台 ping 线程
    ping_thread = threading.Thread(target=_ping_background, daemon=True)
    ping_thread.start()

    print(f"[{now_iso()}] Profiling 监控启动 | 网卡={interface} | 采样=1Hz | "
          f"Ping目标={PING_GW},{PING_EXT}(后台{PING_INTERVAL}s) | 输出={output_dir}")

    # 钉钉启动通知
    try:
        from notifications import notify_service_start
        notify_service_start("带宽采集", f"网卡={interface} 采样=1Hz")
    except Exception:
        pass

    while is_running():
        time.sleep(1.0)
        now_mono = time.monotonic()
        elapsed = now_mono - prev_time

        try:
            cur_rx = read_counter(interface, "rx_bytes")
            cur_tx = read_counter(interface, "tx_bytes")
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            writer.close()
            sys.exit(1)

        rx_delta = cur_rx - prev_rx
        tx_delta = cur_tx - prev_tx
        if rx_delta < 0:
            rx_delta += 2**32
        if tx_delta < 0:
            tx_delta += 2**32

        rx_mbps = bytes_to_mbps(rx_delta, elapsed)
        tx_mbps = bytes_to_mbps(tx_delta, elapsed)

        cpu_load_1m = read_loadavg()

        with _ping_lock:
            rtt_gw = _ping_cache["rtt_gw"]
            loss_gw = _ping_cache["loss_gw"]
            rtt_ext = _ping_cache["rtt_ext"]
            loss_ext = _ping_cache["loss_ext"]

        ts = now_iso()

        if is_daily_cut_time():
            path = writer.flush_and_rotate()
            print(f"[{ts}] 日切完成 → {path}")
            writer.rotate_for_new_day()
            time.sleep(61)
            prev_rx = read_counter(interface, "rx_bytes")
            prev_tx = read_counter(interface, "tx_bytes")
            prev_time = time.monotonic()
            continue

        writer.write_row({
            "timestamp": ts,
            "rx_mbps": rx_mbps,
            "tx_mbps": tx_mbps,
            "cpu_load_1m": cpu_load_1m,
            "rtt_gw_ms": rtt_gw,
            "rtt_ext_ms": rtt_ext,
            "loss_gw_pct": loss_gw,
            "loss_ext_pct": loss_ext,
        })

        prev_rx = cur_rx
        prev_tx = cur_tx
        prev_time = now_mono

    writer.close()
    print(f"[{now_iso()}] 监控已停止。")

    # 钉钉停止通知
    try:
        from notifications import notify_service_stop
        notify_service_stop("带宽采集")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main():
    setup_signal_handler()

    parser = argparse.ArgumentParser(
        description="AWS 网卡带宽 1Hz 采集守护进程"
    )
    parser.add_argument(
        "-i", "--interface", default=INTERFACE,
        help=f"要监控的网卡名称（默认 {INTERFACE}）",
    )
    parser.add_argument(
        "-o", "--output", default=DATA_DIR,
        help=f"CSV 日志输出目录（默认 {DATA_DIR}）",
    )
    args = parser.parse_args()

    if not check_interface(args.interface):
        print(
            f"[FATAL] 网卡 '{args.interface}' 不存在。\n"
            f"请运行 `ip link show` 查看可用网卡，然后用 --interface 指定。",
            file=sys.stderr,
        )
        sys.exit(1)

    run_monitor(interface=args.interface, output_dir=args.output)


if __name__ == "__main__":
    main()
