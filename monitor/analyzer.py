from __future__ import annotations
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
带宽积分机制分析模块

从 bandwidth_analyzer.py 提取的核心分析逻辑：
- CSV 数据加载
- 积分机制反推算法
- 报告生成
- LLM Prompt 构建
"""

import os
import sys
import csv
import bisect
import datetime
import statistics

from config import DATA_DIR, INTERFACE, BUCKETS
from stats import percentile, basic_stats, bucket_distribution
from utils import format_duration


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_csv_files(file_paths: list[str]) -> list[dict]:
    """
    加载一个或多个 CSV 文件，跳过 # 开头的注释行。
    返回按时间排序的记录列表。
    """
    rows = []
    for path in file_paths:
        if not os.path.exists(path):
            print(f"[WARN] 文件不存在，跳过: {path}", file=sys.stderr)
            continue
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(
                (line for line in f if not line.startswith("#")),
            )
            for row in reader:
                try:
                    record = {
                        "timestamp": datetime.datetime.strptime(
                            row["timestamp"].strip(), "%Y-%m-%d %H:%M:%S"
                        ),
                        "rx_mbps": float(row["rx_mbps"]),
                        "tx_mbps": float(row["tx_mbps"]),
                        "cpu_load_1m": float(row["cpu_load_1m"]),
                    }
                    if "rtt_gw_ms" in row:
                        record["rtt_gw_ms"] = float(row["rtt_gw_ms"])
                        record["rtt_ext_ms"] = float(row["rtt_ext_ms"])
                        record["loss_gw_pct"] = float(row["loss_gw_pct"])
                        record["loss_ext_pct"] = float(row["loss_ext_pct"])
                    rows.append(record)
                except (ValueError, KeyError):
                    continue
    rows.sort(key=lambda r: r["timestamp"])
    return rows


def find_latest_file(data_dir: str = None, interface: str = None) -> str | None:
    """找到数据目录中最新的 traffic_log 文件。"""
    data_dir = data_dir or DATA_DIR
    interface = interface or INTERFACE

    if not os.path.exists(data_dir):
        return None

    prefix = "traffic_log_"
    suffix = f"_{interface}.csv"
    candidates = []
    for name in os.listdir(data_dir):
        if name.startswith(prefix) and name.endswith(suffix):
            candidates.append(os.path.join(data_dir, name))
    if not candidates:
        return None
    candidates.sort(key=os.path.getmtime)
    return candidates[-1]


def find_files_in_dir(data_dir: str = None, interface: str = None) -> list[str]:
    """列出数据目录中该网卡的所有 CSV 文件。"""
    data_dir = data_dir or DATA_DIR
    interface = interface or INTERFACE

    if not os.path.exists(data_dir):
        return []

    prefix = "traffic_log_"
    suffix = f"_{interface}.csv"
    files = []
    for name in os.listdir(data_dir):
        if name.startswith(prefix) and name.endswith(suffix):
            files.append(os.path.join(data_dir, name))
    files.sort()
    return files


# ---------------------------------------------------------------------------
# 积分机制反推算法
# ---------------------------------------------------------------------------

def detect_events(rows: list[dict], threshold_mbps: float, min_duration: int,
                  direction: str) -> list[dict]:
    """
    检测连续事件（突发或钳位）。

    direction: "above" — 寻找 > threshold 的连续区间（突发）
               "below" — 寻找 < threshold 的连续区间（钳位）
    """
    events = []
    in_event = False
    event_start = None
    event_values = []

    for row in rows:
        if direction == "above":
            val = max(row["rx_mbps"], row["tx_mbps"])
            condition = val > threshold_mbps
        else:
            val = min(row["rx_mbps"], row["tx_mbps"])
            condition = val < threshold_mbps

        if condition:
            if not in_event:
                in_event = True
                event_start = row["timestamp"]
                event_values = []
            event_values.append(val)
        else:
            if in_event and len(event_values) >= min_duration:
                duration = len(event_values)
                events.append({
                    "start": event_start,
                    "end": event_start + datetime.timedelta(seconds=duration - 1),
                    "duration_sec": duration,
                    "peak": round(max(event_values), 4),
                    "avg": round(statistics.mean(event_values), 4),
                })
            in_event = False
            event_values = []

    # 处理末尾事件
    if in_event and len(event_values) >= min_duration:
        duration = len(event_values)
        events.append({
            "start": event_start,
            "end": event_start + datetime.timedelta(seconds=duration - 1),
            "duration_sec": duration,
            "peak": round(max(event_values), 4),
            "avg": round(statistics.mean(event_values), 4),
        })

    return events


def _rows_in_range(rows: list[dict], timestamps: list[datetime.datetime],
                   start: datetime.datetime, end: datetime.datetime) -> list[dict]:
    """用二分查找提取 [start, end] 区间内的行，O(log n) 定位。"""
    lo = bisect.bisect_left(timestamps, start)
    hi = bisect.bisect_right(timestamps, end)
    return rows[lo:hi]


def infer_credit_cycle(rows: list[dict], timestamps: list[datetime.datetime],
                       burst_events: list[dict],
                       throttle_events: list[dict],
                       sustainable_rx: float, sustainable_tx: float) -> dict:
    """推断积分积累和消耗周期。"""
    result = {
        "total_burst_count": len(burst_events),
        "total_throttle_count": len(throttle_events),
        "avg_burst_duration_sec": 0,
        "max_burst_duration_sec": 0,
        "avg_throttle_duration_sec": 0,
        "max_throttle_duration_sec": 0,
        "estimated_daily_credit_gb": 0.0,
        "burst_to_throttle_pattern": "unknown",
    }

    if burst_events:
        durations = [e["duration_sec"] for e in burst_events]
        result["avg_burst_duration_sec"] = round(statistics.mean(durations))
        result["max_burst_duration_sec"] = max(durations)

    if throttle_events:
        durations = [e["duration_sec"] for e in throttle_events]
        result["avg_throttle_duration_sec"] = round(statistics.mean(durations))
        result["max_throttle_duration_sec"] = max(durations)

    # 估算日积分预算：突发期间超出基线的总流量
    total_excess_bytes = 0
    for evt in burst_events:
        for row in _rows_in_range(rows, timestamps, evt["start"], evt["end"]):
            excess_rx = max(0, row["rx_mbps"] - sustainable_rx)
            excess_tx = max(0, row["tx_mbps"] - sustainable_tx)
            total_excess_bytes += (excess_rx + excess_tx) * 0.125
    result["estimated_daily_credit_mb"] = round(total_excess_bytes, 1)

    # 模式推断
    if len(throttle_events) == 0:
        result["burst_to_throttle_pattern"] = "无明显钳位，积分充足或用量未触及上限"
    elif len(throttle_events) <= 3:
        result["burst_to_throttle_pattern"] = "偶尔钳位，积分基本够用"
    else:
        result["burst_to_throttle_pattern"] = "频繁钳位，积分经常耗尽"

    return result


def reverse_engineer_credits(rows: list[dict]) -> dict:
    """
    核心算法：反推带宽积分机制。
    """
    rx_vals = [r["rx_mbps"] for r in rows]
    tx_vals = [r["tx_mbps"] for r in rows]
    combined = [max(r["rx_mbps"], r["tx_mbps"]) for r in rows]

    # 预构建时间索引
    timestamps = [r["timestamp"] for r in rows]

    # Step A: 可持续基线 — 取 P25 以下的均值
    rx_p25 = percentile(rx_vals, 25)
    tx_p25 = percentile(tx_vals, 25)
    rx_low = [v for v in rx_vals if v < rx_p25]
    tx_low = [v for v in tx_vals if v < tx_p25]
    sustainable_rx = round(statistics.mean(rx_low), 4) if rx_low else round(rx_p25, 4)
    sustainable_tx = round(statistics.mean(tx_low), 4) if tx_low else round(tx_p25, 4)

    # Step B: 可突上限 — 取 P95
    burst_ceiling_rx = round(percentile(rx_vals, 95), 4)
    burst_ceiling_tx = round(percentile(tx_vals, 95), 4)

    # Step C: 钳位检测
    throttle_threshold = max(sustainable_rx, sustainable_tx) * 1.2
    throttle_events = detect_events(rows, throttle_threshold, 60, "below")

    if throttle_events:
        throttle_floor_vals = []
        for evt in throttle_events:
            for row in _rows_in_range(rows, timestamps, evt["start"], evt["end"]):
                throttle_floor_vals.append(min(row["rx_mbps"], row["tx_mbps"]))
        throttle_floor = round(statistics.mean(throttle_floor_vals), 4) if throttle_floor_vals else 0.0
    else:
        throttle_floor = sustainable_rx

    # Step D: 突发事件检测
    p50_combined = percentile(combined, 50)
    burst_events = detect_events(rows, p50_combined, 5, "above")

    # Step E: 积分周期推断
    credit_inference = infer_credit_cycle(rows, timestamps, burst_events,
                                          throttle_events,
                                          sustainable_rx, sustainable_tx)

    return {
        "sustainable_rx": sustainable_rx,
        "sustainable_tx": sustainable_tx,
        "burst_ceiling_rx": burst_ceiling_rx,
        "burst_ceiling_tx": burst_ceiling_tx,
        "throttle_floor": throttle_floor,
        "burst_events": burst_events,
        "throttle_events": throttle_events,
        "credit_inference": credit_inference,
    }


# ---------------------------------------------------------------------------
# ASCII 图表
# ---------------------------------------------------------------------------

def ascii_bar_chart(distribution: list[dict], max_width: int = 30,
                    title: str = "") -> str:
    """生成 ASCII 柱状图。"""
    if not distribution:
        return ""

    lines = []
    if title:
        lines.append(f"  {title}")

    max_pct = max(d["pct"] for d in distribution) if distribution else 1
    for d in distribution:
        bar_len = int(d["pct"] / max_pct * max_width) if max_pct > 0 else 0
        bar = "█" * bar_len
        label = f"  {d['range']:>8s} Mbps"
        lines.append(f"{label}  {bar} {d['pct']:.1f}%")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def generate_report(rows: list[dict], analysis: dict,
                    file_paths: list[str]) -> str:
    """生成 Markdown 格式的分析报告。"""
    rx_vals = [r["rx_mbps"] for r in rows]
    tx_vals = [r["tx_mbps"] for r in rows]
    cpu_vals = [r["cpu_load_1m"] for r in rows]

    rx_stats = basic_stats(rx_vals, "Rx (下行)")
    tx_stats = basic_stats(tx_vals, "Tx (上行)")
    cpu_stats = basic_stats(cpu_vals, "CPU Load")

    rx_dist = bucket_distribution(rx_vals, BUCKETS)
    tx_dist = bucket_distribution(tx_vals, BUCKETS)

    first_ts = rows[0]["timestamp"].strftime("%Y-%m-%d %H:%M")
    last_ts = rows[-1]["timestamp"].strftime("%Y-%m-%d %H:%M")
    days = (rows[-1]["timestamp"] - rows[0]["timestamp"]).total_seconds() / 86400

    ci = analysis["credit_inference"]

    report = f"""# 🔍 带宽积分机制分析报告

## 📅 分析周期
- **起止时间**: {first_ts} ~ {last_ts}
- **跨度**: {days:.1f} 天
- **数据量**: {len(rows):,} 条采样
- **文件数**: {len(file_paths)}

---

## 📊 带宽统计总览

| 指标 | Rx (下行) | Tx (上行) | CPU Load |
|------|-----------|-----------|----------|
| 最小值 | {rx_stats['min']} Mbps | {tx_stats['min']} Mbps | {cpu_stats['min']} |
| 最大值 | {rx_stats['max']} Mbps | {tx_stats['max']} Mbps | {cpu_stats['max']} |
| 均值 | {rx_stats['mean']} Mbps | {tx_stats['mean']} Mbps | {cpu_stats['mean']} |
| 中位数 | {rx_stats['median']} Mbps | {tx_stats['median']} Mbps | {cpu_stats['median']} |
| 标准差 | {rx_stats['std']} Mbps | {tx_stats['std']} Mbps | {cpu_stats['std']} |
| P5 | {rx_stats['p5']} Mbps | {tx_stats['p5']} Mbps | {cpu_stats['p5']} |
| P25 | {rx_stats['p25']} Mbps | {tx_stats['p25']} Mbps | - |
| P75 | {rx_stats['p75']} Mbps | {tx_stats['p75']} Mbps | - |
| P95 | {rx_stats['p95']} Mbps | {tx_stats['p95']} Mbps | {cpu_stats['p95']} |
| P99 | {rx_stats['p99']} Mbps | {tx_stats['p99']} Mbps | {cpu_stats['p99']} |

---

## 🎯 积分机制推断

### 可持续带宽（基线）
- **下行 (Rx)**: {analysis['sustainable_rx']} Mbps
- **上行 (Tx)**: {analysis['sustainable_tx']} Mbps

### 可突带宽（上限）
- **下行 (Rx)**: {analysis['burst_ceiling_rx']} Mbps
- **上行 (Tx)**: {analysis['burst_ceiling_tx']} Mbps

### 钳位地板值
- **被限速时的带宽**: {analysis['throttle_floor']} Mbps

---

## ⚡ 突发事件统计
- **突发次数**: {ci['total_burst_count']} 次
- **平均持续**: {format_duration(ci['avg_burst_duration_sec'])}
- **最长突发**: {format_duration(ci['max_burst_duration_sec'])}
- **估算日超额流量**: {ci['estimated_daily_credit_mb']} MB

## 🔒 钳位事件统计
- **钳位次数**: {ci['total_throttle_count']} 次
- **平均持续**: {format_duration(ci['avg_throttle_duration_sec'])}
- **最长钳位**: {format_duration(ci['max_throttle_duration_sec'])}

## 📈 积分模式推断
- **判断**: {ci['burst_to_throttle_pattern']}

---

## 📉 带宽分布 (Rx 下行)

{ascii_bar_chart(rx_dist, max_width=30)}

## 📉 带宽分布 (Tx 上行)

{ascii_bar_chart(tx_dist, max_width=30)}
"""

    # 延迟统计（新版 CSV 才有）
    has_latency = "rtt_gw_ms" in rows[0]
    if has_latency:
        rtt_gw_vals = [r["rtt_gw_ms"] for r in rows if r["rtt_gw_ms"] > 0]
        rtt_ext_vals = [r["rtt_ext_ms"] for r in rows if r["rtt_ext_ms"] > 0]
        loss_gw_vals = [r["loss_gw_pct"] for r in rows]
        loss_ext_vals = [r["loss_ext_pct"] for r in rows]

        if rtt_gw_vals or rtt_ext_vals:
            rtt_gw_stats = basic_stats(rtt_gw_vals, "RTT GW") if rtt_gw_vals else {}
            rtt_ext_stats = basic_stats(rtt_ext_vals, "RTT EXT") if rtt_ext_vals else {}
            loss_gw_avg = round(statistics.mean(loss_gw_vals), 2) if loss_gw_vals else 0
            loss_ext_avg = round(statistics.mean(loss_ext_vals), 2) if loss_ext_vals else 0

            # 延迟突增检测
            latency_spikes = []
            if rtt_ext_vals and len(rtt_ext_vals) > 1:
                ext_mean = statistics.mean(rtt_ext_vals)
                ext_std = statistics.stdev(rtt_ext_vals)
                spike_threshold = ext_mean + 3 * ext_std
                for row in rows:
                    if row.get("rtt_ext_ms", 0) > spike_threshold and row["rtt_ext_ms"] > 0:
                        latency_spikes.append({
                            "time": row["timestamp"].strftime("%m-%d %H:%M:%S"),
                            "rtt_ext": row["rtt_ext_ms"],
                            "rtt_gw": row.get("rtt_gw_ms", 0),
                            "rx": row["rx_mbps"],
                            "tx": row["tx_mbps"],
                        })

            report += f"""
---

## 📡 延迟统计

| 指标 | AWS 网关 (内网) | 公网 (114.114.114.114) |
|------|-----------------|----------------------|
| 均值 | {rtt_gw_stats.get('mean', 'N/A')} ms | {rtt_ext_stats.get('mean', 'N/A')} ms |
| 中位数 | {rtt_gw_stats.get('median', 'N/A')} ms | {rtt_ext_stats.get('median', 'N/A')} ms |
| P95 | {rtt_gw_stats.get('p95', 'N/A')} ms | {rtt_ext_stats.get('p95', 'N/A')} ms |
| P99 | {rtt_gw_stats.get('p99', 'N/A')} ms | {rtt_ext_stats.get('p99', 'N/A')} ms |
| 最大值 | {rtt_gw_stats.get('max', 'N/A')} ms | {rtt_ext_stats.get('max', 'N/A')} ms |
| 平均丢包 | {loss_gw_avg}% | {loss_ext_avg}% |

"""
            if latency_spikes:
                report += f"### ⚠️ 延迟突增事件 (>{spike_threshold:.1f}ms, 共 {len(latency_spikes)} 次)\n\n"
                report += "| 时间 | 公网RTT | 内网RTT | Rx | Tx |\n"
                report += "|------|---------|---------|-----|-----|\n"
                for spike in latency_spikes[:20]:
                    report += (
                        f"| {spike['time']} | {spike['rtt_ext']} ms "
                        f"| {spike['rtt_gw']} ms | {spike['rx']} Mbps | {spike['tx']} Mbps |\n"
                    )
                if len(latency_spikes) > 20:
                    report += f"\n> 共 {len(latency_spikes)} 次突增，仅显示前 20 次\n"

            if rtt_ext_stats and rtt_ext_stats.get("std", 0) > 10:
                report += "\n> 🔍 **公网延迟标准差过大 ({:.1f}ms)，可能存在动态路由切换**\n".format(
                    rtt_ext_stats["std"]
                )

    # 逐日对比
    if days >= 1.5:
        has_latency = "rtt_gw_ms" in rows[0]
        if has_latency:
            report += "\n---\n\n## 📅 逐日峰值对比\n\n"
            report += "| 日期 | Rx 峰值 | Tx 峰值 | CPU 峰值 | 最大RTT(公网) |\n"
            report += "|------|---------|---------|----------|---------------|\n"
        else:
            report += "\n---\n\n## 📅 逐日峰值对比\n\n"
            report += "| 日期 | Rx 峰值 | Tx 峰值 | CPU 峰值 |\n"
            report += "|------|---------|---------|----------|\n"
        daily = {}
        for row in rows:
            d = row["timestamp"].strftime("%Y-%m-%d")
            if d not in daily:
                daily[d] = {"rx": 0, "tx": 0, "cpu": 0, "rtt_ext": 0}
            daily[d]["rx"] = max(daily[d]["rx"], row["rx_mbps"])
            daily[d]["tx"] = max(daily[d]["tx"], row["tx_mbps"])
            daily[d]["cpu"] = max(daily[d]["cpu"], row["cpu_load_1m"])
            if has_latency:
                daily[d]["rtt_ext"] = max(daily[d]["rtt_ext"], row.get("rtt_ext_ms", 0))
        for d in sorted(daily.keys()):
            v = daily[d]
            if has_latency:
                report += f"| {d} | {v['rx']:.2f} Mbps | {v['tx']:.2f} Mbps | {v['cpu']:.2f} | {v['rtt_ext']:.1f} ms |\n"
            else:
                report += f"| {d} | {v['rx']:.2f} Mbps | {v['tx']:.2f} Mbps | {v['cpu']:.2f} |\n"

    return report


# ---------------------------------------------------------------------------
# LLM Prompt 构建
# ---------------------------------------------------------------------------

def _build_latency_prompt_section(rows: list[dict]) -> str:
    """构建延迟数据的 Prompt 段落。"""
    if not rows or "rtt_gw_ms" not in rows[0]:
        return ""

    rtt_gw_vals = [r["rtt_gw_ms"] for r in rows if r["rtt_gw_ms"] > 0]
    rtt_ext_vals = [r["rtt_ext_ms"] for r in rows if r["rtt_ext_ms"] > 0]
    loss_gw_vals = [r["loss_gw_pct"] for r in rows]
    loss_ext_vals = [r["loss_ext_pct"] for r in rows]

    if not rtt_gw_vals and not rtt_ext_vals:
        return ""

    section = "### 延迟\n"
    if rtt_gw_vals:
        section += f"- AWS 网关 RTT: 均值 {round(statistics.mean(rtt_gw_vals),1)}ms, P95 {round(percentile(rtt_gw_vals,95),1)}ms, 最大 {round(max(rtt_gw_vals),1)}ms\n"
        section += f"- AWS 网关丢包: {round(statistics.mean(loss_gw_vals),2)}%\n"
    if rtt_ext_vals:
        section += f"- 公网 RTT: 均值 {round(statistics.mean(rtt_ext_vals),1)}ms, P95 {round(percentile(rtt_ext_vals,95),1)}ms, 最大 {round(max(rtt_ext_vals),1)}ms\n"
        section += f"- 公网丢包: {round(statistics.mean(loss_ext_vals),2)}%\n"
        ext_std = round(statistics.stdev(rtt_ext_vals), 1) if len(rtt_ext_vals) > 1 else 0
        if ext_std > 10:
            section += f"- ⚠️ 公网延迟标准差 {ext_std}ms，可能存在路由切换\n"

    return section


def _build_hourly_stats(rows: list[dict]) -> str:
    """构建逐小时带宽统计。"""
    if not rows:
        return ""

    hourly = {}
    for row in rows:
        hour = row["timestamp"].strftime("%Y-%m-%d %H:00")
        if hour not in hourly:
            hourly[hour] = {"rx": [], "tx": [], "cpu": []}
        hourly[hour]["rx"].append(row["rx_mbps"])
        hourly[hour]["tx"].append(row["tx_mbps"])
        hourly[hour]["cpu"].append(row["cpu_load_1m"])

    lines = ["## 逐小时带宽统计\n"]
    lines.append("| 时间 | Rx均值 | Rx峰值 | Tx均值 | Tx峰值 | CPU均值 |")
    lines.append("|------|--------|--------|--------|--------|---------|")

    for hour in sorted(hourly.keys()):
        h = hourly[hour]
        lines.append(
            f"| {hour} "
            f"| {round(statistics.mean(h['rx']),2)} Mbps "
            f"| {round(max(h['rx']),2)} Mbps "
            f"| {round(statistics.mean(h['tx']),2)} Mbps "
            f"| {round(max(h['tx']),2)} Mbps "
            f"| {round(statistics.mean(h['cpu']),3)} |"
        )

    return "\n".join(lines)


def _build_latency_correlation(rows: list[dict]) -> str:
    """分析延迟与带宽的关联性。"""
    if not rows or "rtt_ext_ms" not in rows[0]:
        return ""

    valid = [r for r in rows if r.get("rtt_ext_ms", -1) > 0]
    if len(valid) < 10:
        return ""

    combined = [(max(r["rx_mbps"], r["tx_mbps"]), r["rtt_ext_ms"]) for r in valid]
    combined.sort(key=lambda x: x[0])

    n = len(combined)
    low_bw = combined[:n // 4]
    high_bw = combined[-n // 4:]

    low_rtt = [x[1] for x in low_bw]
    high_rtt = [x[1] for x in high_bw]

    low_avg = round(statistics.mean(low_rtt), 1)
    high_avg = round(statistics.mean(high_rtt), 1)
    diff = round(high_avg - low_avg, 1)

    lines = ["## 延迟-带宽关联分析\n"]
    lines.append(f"- 低带宽时(下25%)平均延迟: {low_avg}ms")
    lines.append(f"- 高带宽时(上25%)平均延迟: {high_avg}ms")
    lines.append(f"- 差值: {diff}ms")

    if diff > 20:
        lines.append("- ⚠️ 高带宽时延迟明显升高，可能存在拥塞或限速")
    elif diff > 5:
        lines.append("- 📊 高带宽时延迟略有升高，属于正常范围")
    else:
        lines.append("- ✅ 带宽变化对延迟影响不大")

    return "\n".join(lines)


def build_llm_prompt(analysis: dict, rx_stats: dict, tx_stats: dict,
                     cpu_stats: dict, days: float, total_rows: int,
                     rows: list[dict] = None) -> str:
    """构建发给 Mimo 的分析 Prompt。"""
    ci = analysis["credit_inference"]

    # 突发事件摘要
    burst_summary = ""
    for i, evt in enumerate(analysis["burst_events"][:20]):
        burst_summary += (
            f"  {i+1}. {evt['start'].strftime('%m-%d %H:%M')} ~ "
            f"{evt['end'].strftime('%H:%M')}, "
            f"持续{evt['duration_sec']}秒, 峰值{evt['peak']}Mbps\n"
        )
    if len(analysis["burst_events"]) > 20:
        burst_summary += f"  ... 共 {len(analysis['burst_events'])} 次突发\n"

    # 钳位事件摘要
    throttle_summary = ""
    for i, evt in enumerate(analysis["throttle_events"][:20]):
        throttle_summary += (
            f"  {i+1}. {evt['start'].strftime('%m-%d %H:%M')} ~ "
            f"{evt['end'].strftime('%H:%M')}, "
            f"持续{evt['duration_sec']}秒, 均值{evt['avg']}Mbps\n"
        )
    if len(analysis["throttle_events"]) > 20:
        throttle_summary += f"  ... 共 {len(analysis['throttle_events'])} 次钳位\n"

    hourly_stats = _build_hourly_stats(rows) if rows else ""
    latency_correlation = _build_latency_correlation(rows) if rows else ""

    prompt = f"""你是一位资深的云计算和 AIOps 专家，精通 AWS Lightsail 的带宽积分机制。
请对以下服务器的带宽监控数据进行全面深入的分析。

## 服务器信息
- 厂商: AWS Lightsail（$7/月套餐，512MB 内存）
- 分析周期: {days:.1f} 天
- 数据量: {total_rows:,} 条 1Hz 采样
- 采集间隔: 每秒 1 行，无降噪，无省略

## 带宽统计

### 下行 (Rx)
- 最小值: {rx_stats['min']} Mbps, 最大值: {rx_stats['max']} Mbps
- 均值: {rx_stats['mean']} Mbps, 中位数: {rx_stats['median']} Mbps
- 标准差: {rx_stats['std']} Mbps
- P5: {rx_stats['p5']}, P25: {rx_stats['p25']}, P50: {rx_stats['median']}, P75: {rx_stats['p75']}, P95: {rx_stats['p95']}, P99: {rx_stats['p99']}

### 上行 (Tx)
- 最小值: {tx_stats['min']} Mbps, 最大值: {tx_stats['max']} Mbps
- 均值: {tx_stats['mean']} Mbps, 中位数: {tx_stats['median']} Mbps
- 标准差: {tx_stats['std']} Mbps
- P5: {tx_stats['p5']}, P25: {tx_stats['p25']}, P50: {tx_stats['median']}, P75: {tx_stats['p75']}, P95: {tx_stats['p95']}, P99: {tx_stats['p99']}

### CPU 负载
- 均值: {cpu_stats['mean']}, 最大值: {cpu_stats['max']}, P95: {cpu_stats['p95']}, 标准差: {cpu_stats['std']}

{_build_latency_prompt_section(rows) if rows else ""}

{hourly_stats}

{latency_correlation}

## 算法推断结果
- 可持续下行基线: {analysis['sustainable_rx']} Mbps
- 可持续上行基线: {analysis['sustainable_tx']} Mbps
- 可突下行上限: {analysis['burst_ceiling_rx']} Mbps
- 可突上行上限: {analysis['burst_ceiling_tx']} Mbps
- 钳位地板值: {analysis['throttle_floor']} Mbps
- 突发→钳位模式: {ci['burst_to_throttle_pattern']}

## 突发事件记录（最近 {min(len(analysis['burst_events']), 20)} 条）
{burst_summary if burst_summary else "  无"}

## 钳位事件记录（最近 {min(len(analysis['throttle_events']), 20)} 条）
{throttle_summary if throttle_summary else "  无"}

## 积分统计
- 突发总次数: {ci['total_burst_count']}
- 平均突发持续: {ci['avg_burst_duration_sec']}秒, 最长: {ci['max_burst_duration_sec']}秒
- 钳位总次数: {ci['total_throttle_count']}
- 平均钳位持续: {ci['avg_throttle_duration_sec']}秒, 最长: {ci['max_throttle_duration_sec']}秒
- 估算日超额流量: {ci['estimated_daily_credit_mb']} MB

---

请完成以下深度分析（用结构化的中文）：

1. **带宽积分机制判断**
   - 这个 Lightsail 套餐是否有带宽积分机制？判断依据是什么？
   - 如果有，积分是按时间积累还是按流量配额？池子容量大约多大？

2. **可持续/可突带宽评估**
   - 我推断的基线（{analysis['sustainable_rx']}/{analysis['sustainable_tx']} Mbps）是否合理？
   - 我推断的突上限（{analysis['burst_ceiling_rx']}/{analysis['burst_ceiling_tx']} Mbps）是否合理？

3. **延迟与路由分析**
   - 延迟数据是否稳定？是否存在路由切换的迹象？
   - 带宽高的时候延迟是否同步升高？

4. **积分周期推断**
   - 积分大概多久能充满？能支撑多久的突发？耗尽后多久恢复？

5. **使用模式分析**
   - 当前的流量模式是什么样的？哪些时段是高风险时段？

6. **优化建议**
   - 任务调度：什么时间段适合跑大流量任务？
   - 流量整形：如何避免触发限速？
   - 套餐升级：当前套餐是否够用？

7. **风险评估**
   - 当前模式下，触发限速的概率有多大？"""

    return prompt


# ---------------------------------------------------------------------------
# CLI 入口（供 systemd timer 调用）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="带宽积分分析")
    parser.add_argument("--auto", action="store_true", help="自动分析最新数据")
    parser.add_argument("--files", nargs="+", help="指定文件")
    parser.add_argument("--no-dingtalk", action="store_true", help="不推送钉钉")
    parser.add_argument("--no-llm", action="store_true", help="不调用 LLM")
    args = parser.parse_args()

    from stats import basic_stats
    from notifications import send_dingtalk, call_xfyun
    from config import XFYUN_ENABLED

    if args.auto:
        latest = find_latest_file()
        if not latest:
            print("[WARN] 未找到数据文件", file=sys.stderr)
            sys.exit(0)
        file_paths = [latest]
    elif args.files:
        file_paths = args.files
    else:
        print("用法: analyzer.py --auto 或 --files f1.csv f2.csv", file=sys.stderr)
        sys.exit(1)

    rows = load_csv_files(file_paths)
    if not rows:
        print("[WARN] 没有有效数据", file=sys.stderr)
        sys.exit(0)

    analysis = reverse_engineer_credits(rows)
    report = generate_report(rows, analysis, file_paths)

    # LLM 分析
    if XFYUN_ENABLED and not args.no_llm:
        rx_vals = [r["rx_mbps"] for r in rows]
        tx_vals = [r["tx_mbps"] for r in rows]
        cpu_vals = [r["cpu_load_1m"] for r in rows]
        days = (rows[-1]["timestamp"] - rows[0]["timestamp"]).total_seconds() / 86400
        prompt = build_llm_prompt(
            analysis,
            basic_stats(rx_vals, "Rx"),
            basic_stats(tx_vals, "Tx"),
            basic_stats(cpu_vals, "CPU"),
            days,
            len(rows),
            rows=rows,
        )
        llm_result = call_xfyun(prompt)
        if llm_result:
            report += "\n---\n\n## AI 深度分析\n\n" + llm_result + "\n"

    # 输出
    print(report)

    # 钉钉推送
    if not args.no_dingtalk:
        send_dingtalk("带宽积分分析报告", report)
