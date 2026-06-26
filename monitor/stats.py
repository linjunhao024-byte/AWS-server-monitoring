#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计计算函数模块

从 bandwidth_analyzer.py 提取的纯数学函数。
"""

from __future__ import annotations

import statistics


def percentile(data: list[float], p: float) -> float:
    """计算百分位数（线性插值法）。"""
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[-1]
    return s[f] + (k - f) * (s[c] - s[f])


def basic_stats(values: list[float], label: str) -> dict:
    """计算基础统计指标。"""
    if not values:
        return {}
    return {
        "label": label,
        "count": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.mean(values), 4),
        "median": round(statistics.median(values), 4),
        "std": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
        "p5": round(percentile(values, 5), 4),
        "p25": round(percentile(values, 25), 4),
        "p75": round(percentile(values, 75), 4),
        "p95": round(percentile(values, 95), 4),
        "p99": round(percentile(values, 99), 4),
    }


def bucket_distribution(values: list[float], buckets: list[tuple]) -> list[dict]:
    """将数据分桶，统计每个桶的时间占比。"""
    total = len(values)
    if total == 0:
        return []
    result = []
    for lo, hi in buckets:
        count = sum(1 for v in values if lo <= v < hi)
        pct = count / total * 100
        label = f"{lo}~{hi}" if hi != float("inf") else f"{lo}+"
        result.append({"range": label, "count": count, "pct": round(pct, 1)})
    return result
