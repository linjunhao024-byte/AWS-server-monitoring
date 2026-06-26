#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统数据读取模块

统一的数据源接口：sysfs 网卡计数器、/proc/loadavg、ping、traceroute、vnstat。
"""

from __future__ import annotations

import os
import re
import sys
import json
import subprocess

from config import INTERFACE, PING_COUNT, PING_TIMEOUT


# ---------------------------------------------------------------------------
# sysfs 网卡计数器
# ---------------------------------------------------------------------------

def read_counter(interface: str, stat_name: str) -> int:
    """
    读取 /sys/class/net/{interface}/statistics/{stat_name}，返回整数值。
    """
    path = f"/sys/class/net/{interface}/statistics/{stat_name}"
    try:
        with open(path, "r") as fh:
            return int(fh.read().strip())
    except FileNotFoundError:
        raise RuntimeError(
            f"[FATAL] 网卡 '{interface}' 不存在或无法读取 {path}。"
            f"请用 `ip link show` 确认可用网卡名称。"
        )
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"[FATAL] 读取 {path} 失败: {exc}")


# 虚拟网卡前缀/名称黑名单
_VIRTUAL_IFACE_PATTERNS = (
    "lo", "docker", "br-", "veth", "virbr", "vmnet",
    "tun", "tap", "ppp", "wg", "CloudflareWARP", "warp",
    "zt", "tailscale", "utun",
)


def is_virtual_interface(interface: str) -> bool:
    """判断是否为虚拟/容器/VPN 网卡。"""
    for pat in _VIRTUAL_IFACE_PATTERNS:
        if interface == pat or interface.startswith(pat):
            return True
    return False


def list_physical_interfaces() -> list[str]:
    """列出所有物理/可用网卡（排除虚拟接口）。"""
    net_dir = "/sys/class/net"
    if not os.path.exists(net_dir):
        return []
    result = []
    for name in os.listdir(net_dir):
        if is_virtual_interface(name):
            continue
        if os.path.exists(f"{net_dir}/{name}/statistics/rx_bytes"):
            result.append(name)
    return sorted(result)


def check_interface(interface: str) -> bool:
    """检查网卡是否存在。"""
    return os.path.exists(f"/sys/class/net/{interface}/statistics/rx_bytes")


# ---------------------------------------------------------------------------
# /proc/loadavg
# ---------------------------------------------------------------------------

def read_loadavg() -> float:
    """读取 /proc/loadavg 第一个字段（1 分钟平均负载）。"""
    try:
        with open("/proc/loadavg", "r") as fh:
            return float(fh.read().split()[0])
    except (FileNotFoundError, OSError, ValueError, IndexError):
        return 0.0


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------

def ping_once(target: str, count: int = None, timeout: int = None) -> tuple[float, float]:
    """
    ping 一次目标，返回 (loss_pct, rtt_ms)。

    Args:
        target: ping 目标地址
        count: ping 次数（默认使用配置）
        timeout: 超时秒数（默认使用配置）

    Returns:
        (丢包率百分比, RTT毫秒) — 超时时 rtt_ms 返回 -1
    """
    count = count or PING_COUNT
    timeout = timeout or PING_TIMEOUT

    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout), target],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        output = result.stdout

        # 解析丢包率
        loss_match = re.search(r"(\d+)% packet loss", output)
        loss_pct = float(loss_match.group(1)) if loss_match else 100.0

        # 解析 RTT
        rtt_match = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", output)
        rtt_ms = float(rtt_match.group(1)) if rtt_match else -1.0

        return (loss_pct, round(rtt_ms, 2))
    except (subprocess.TimeoutExpired, Exception):
        return (100.0, -1.0)


# ---------------------------------------------------------------------------
# Traceroute
# ---------------------------------------------------------------------------

def run_traceroute(target: str, attempts: int = 3) -> list[str]:
    """
    执行多次 traceroute，取最稳定的路径。

    Args:
        target: 目标 IP
        attempts: 执行次数，取出现最多的路径

    Returns:
        路径节点列表，每个元素格式: "hop_num ip"
    """
    all_paths = []
    for _ in range(attempts):
        try:
            result = subprocess.run(
                ["traceroute", "-n", "-m", "15", "-w", "2", "-q", "1", target],
                capture_output=True, text=True, timeout=60,
            )
            hops = []
            for line in result.stdout.strip().split("\n")[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    hop_num = parts[0]
                    ip = parts[1]
                    if ip != "*":
                        hops.append(f"{hop_num} {ip}")
            if hops:
                all_paths.append(tuple(hops))
        except (subprocess.TimeoutExpired, Exception):
            pass

    if not all_paths:
        return []

    # 取出现次数最多的路径
    from collections import Counter
    counter = Counter(all_paths)
    most_common = counter.most_common(1)[0][0]
    return list(most_common)


def compare_routes(old: list[str], new: list[str]) -> dict:
    """
    比较两次路由路径，返回变化详情。
    只比较 IP 地址，忽略 hop 编号差异。
    """
    # 提取纯 IP 列表（忽略 hop 编号）
    old_ips = [h.split()[1] if " " in h else h for h in old]
    new_ips = [h.split()[1] if " " in h else h for h in new]

    old_set = set(old_ips)
    new_set = set(new_ips)

    added = new_set - old_set
    removed = old_set - new_set

    if not added and not removed:
        return {"changed": False}

    # 找出变化发生在哪一跳
    change_hop = "unknown"
    max_len = max(len(old_ips), len(new_ips))
    for i in range(max_len):
        old_ip = old_ips[i] if i < len(old_ips) else "*"
        new_ip = new_ips[i] if i < len(new_ips) else "*"
        if old_ip != new_ip:
            change_hop = f"hop {i + 1}"
            break

    return {
        "changed": True,
        "added": sorted(added),
        "removed": sorted(removed),
        "change_hop": change_hop,
        "old_hops": len(old),
        "new_hops": len(new),
    }


# ---------------------------------------------------------------------------
# vnstat
# ---------------------------------------------------------------------------

def get_vnstat_json(interface: str = None) -> dict | None:
    """
    获取 vnstat JSON 数据。

    Args:
        interface: 网卡名称（默认使用配置）

    Returns:
        vnstat JSON 数据，失败返回 None
    """
    interface = interface or INTERFACE

    try:
        cmd = ["vnstat", "--json", "d", "-i", interface]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            # 尝试不指定网卡
            cmd = ["vnstat", "--json", "d"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return None

        return json.loads(result.stdout)
    except FileNotFoundError:
        print("[错误] vnstat 命令未找到", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"[错误] JSON 解析失败: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[错误] 获取 vnstat 数据异常: {e}", file=sys.stderr)
        return None


def parse_vnstat_data(data: dict, interface: str = None) -> dict:
    """
    解析 vnstat JSON 数据，返回日流量字典。

    Returns:
        { "YYYY-MM-DD": {"rx": int, "tx": int}, ... }
    """
    if not data:
        return {}

    interface = interface or INTERFACE
    interfaces = data.get("interfaces", [])
    if not interfaces:
        return {}

    # 查找目标网卡
    target_iface = None
    for iface in interfaces:
        if iface.get("name") == interface:
            target_iface = iface
            break

    if not target_iface and len(interfaces) > 0:
        target_iface = interfaces[0]

    if not target_iface:
        return {}

    traffic = target_iface.get("traffic", {})
    days = traffic.get("day", [])

    daily_dict = {}
    for day in days:
        date = day.get("date", {})
        date_str = f"{date.get('year')}-{date.get('month'):02d}-{date.get('day'):02d}"
        daily_dict[date_str] = {
            "rx": day.get("rx", 0),
            "tx": day.get("tx", 0),
        }

    return daily_dict
