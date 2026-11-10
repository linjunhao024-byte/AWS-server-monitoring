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
        ping_start = time.monotonic()
        loss_gw, rtt_gw = ping_once(PING_GW)
        loss_ext, rtt_ext = ping_once(PING_EXT)
        with _ping_lock:
            _ping_cache["rtt_gw"] = rtt_gw
            _ping_cache["loss_gw"] = loss_gw
            _ping_cache["rtt_ext"] = rtt_ext
            _ping_cache["loss_ext"] = loss_ext
        # ping 本身耗时 2-4 秒，剩余时间补足到 PING_INTERVAL
        elapsed_ping = time.monotonic() - ping_start
        time.sleep(max(1, PING_INTERVAL - elapsed_ping))


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
        if file_exists:
            # 同一天内服务重启过：接续已有的计数和峰值，
            # 否则当天摘要只会反映最后一次运行的数据
            self._reload_stats()

    def _reload_stats(self):
        """从已存在的当天文件里重建采样数 / 峰值。"""
        try:
            with open(self._file_path(), "r", encoding="utf-8") as f:
                f.readline()  # 表头
                for line in f:
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.rstrip("\r\n").split(",")
                    if len(parts) < len(self.HEADER):
                        continue
                    try:
                        rx = float(parts[1])
                        tx = float(parts[2])
                    except ValueError:
                        continue
                    self._total_records += 1
                    if tx > self._peak_tx["value"]:
                        self._peak_tx = {"value": tx, "time": parts[0]}
                    if rx > self._peak_rx["value"]:
                        self._peak_rx = {"value": rx, "time": parts[0]}
        except OSError:
            pass

    def current_label(self) -> str:
        """当前正在写入的日期标签（YYYYMMDD）。"""
        return self._date_label

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
        if self._file and self._total_records > 0:
            self._file.write("\n# ===== DAILY SUMMARY (LIN-Monitor) =====\n")
            self._file.write(f"# date: {self._date_label}\n")
            self._file.write(f"# interface: {self._interface}\n")
            self._file.write(f"# total_records: {self._total_records}\n")
            self._file.write(f"# peak_tx_mbps: {self._peak_tx['value']}\n")
            self._file.write(f"# peak_tx_time: {self._peak_tx['time']}\n")
            self._file.write(f"# peak_rx_mbps: {self._peak_rx['value']}\n")
            self._file.write(f"# peak_rx_time: {self._peak_rx['time']}\n")
            self._file.write("# ===== END SUMMARY =====\n")
        if self._file:
            self._file.close()
        self._file = None
        self._writer = None
        return path

    def rotate_for_new_day(self, label: str = None):
        """切换到指定日期的文件。

        label 由调用方传入（就是"现在"的日期），不再自己读时钟 ——
        否则在 23:59 切完、跨过零点后写进来的其实是次日数据，
        却还挂在当天的文件名上。
        """
        if self._file:
            self.flush_and_rotate()
        self._date_label = label or today_label()
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
# 不再用 "23:59 这一分钟精确命中" 的方式判断日切：1Hz 循环只要被 IO / ping
# 线程 / GC 拖过那一分钟，当天就不会切文件。改成比较"当前日期是否已变化"，
# 任何时刻恢复循环都能立刻补上切换。


def needs_rotation(current_label: str) -> bool:
    """当前日期与文件标签不一致就需要切文件。"""
    return today_label() != current_label


def compute_deltas(cur_rx: int, cur_tx: int, prev_rx: int, prev_tx: int):
    """计算两次采样之间的字节增量。

    返回 (rx_delta, tx_delta)；任一方向出现负增量说明 64 位计数器被重置
    （重启 / 网卡 reset），此时返回 None —— 这一秒的速率不可信，
    不能按 32 位回绕硬凑，否则会写出一个巨大的假峰值。
    """
    rx_delta = cur_rx - prev_rx
    tx_delta = cur_tx - prev_tx
    if rx_delta < 0 or tx_delta < 0:
        return None
    return rx_delta, tx_delta


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

    print(f"[{now_iso()}] LIN-Monitor 带宽采集启动 | 网卡={interface} | 采样=1Hz | "
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

        # 64 位计数器在重启 / 网卡重置 / 驱动 reload 后会归零，差值为负。
        # 这里不能按 2^32 回绕（Linux 还在 64 位空间），否则会写入巨大的负速率
        # 污染当天的峰值与积分反推：本次采样直接丢弃并重新对齐基线。
        deltas = compute_deltas(cur_rx, cur_tx, prev_rx, prev_tx)
        if deltas is None:
            print(f"[{now_iso()}] 计数器重置（rx {prev_rx}→{cur_rx} / tx "
                  f"{prev_tx}→{cur_tx}），本次采样跳过", file=sys.stderr)
            prev_rx, prev_tx = cur_rx, cur_tx
            prev_time = now_mono
            continue
        rx_delta, tx_delta = deltas

        rx_mbps = bytes_to_mbps(rx_delta, elapsed)
        tx_mbps = bytes_to_mbps(tx_delta, elapsed)

        cpu_load_1m = read_loadavg()

        with _ping_lock:
            rtt_gw = _ping_cache["rtt_gw"]
            loss_gw = _ping_cache["loss_gw"]
            rtt_ext = _ping_cache["rtt_ext"]
            loss_ext = _ping_cache["loss_ext"]

        ts = now_iso()

        if needs_rotation(writer.current_label()):
            today = today_label()
            prev_path = writer.flush_and_rotate()
            print(f"[{ts}] 日切完成 → {prev_path}")
            writer.rotate_for_new_day(today)
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
    print(f"[{now_iso()}] LIN-Monitor 带宽采集已停止。")

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
