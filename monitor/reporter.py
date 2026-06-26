from __future__ import annotations
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
流量报表模块

从 traffic_monitor_v2.py 提取的报表逻辑：
- vnstat 流量数据获取
- 日报/周报生成
- 同比分析
- 趋势图生成
"""

import os
import sys
from datetime import datetime, timedelta

from config import (
    SERVER_ALIAS, INTERFACE, WEEKLY_REPORT_DAY, CHART_SAVE_PATH,
)
from utils import (
    format_bytes, get_server_ip, get_iso_week_info,
    calculate_growth_rate, format_growth_rate,
)
from data_sources import get_vnstat_json, parse_vnstat_data

# 尝试导入 matplotlib（可选）
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

class TrafficData:
    """流量数据类"""
    def __init__(self):
        self.today_rx = 0
        self.today_tx = 0
        self.today_total = 0
        self.weekly_rx = 0
        self.weekly_tx = 0
        self.weekly_total = 0
        self.daily_data = []
        self.days_counted = 0
        self.missing_dates = []
        self.warnings = []
        self.yesterday_rx = 0
        self.yesterday_tx = 0
        self.last_week_rx = 0
        self.last_week_tx = 0


# ---------------------------------------------------------------------------
# 数据获取
# ---------------------------------------------------------------------------

def get_traffic_data(interface: str = None) -> TrafficData | None:
    """获取今日和本周的流量数据。"""
    interface = interface or INTERFACE
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    week_info = get_iso_week_info()
    week_start = week_info["week_start"]

    vnstat_data = get_vnstat_json(interface)
    if not vnstat_data:
        return None

    daily_dict = parse_vnstat_data(vnstat_data, interface)
    if not daily_dict:
        return None

    traffic = TrafficData()

    # 今日数据
    if today_str in daily_dict:
        traffic.today_rx = daily_dict[today_str]["rx"]
        traffic.today_tx = daily_dict[today_str]["tx"]
        traffic.today_total = traffic.today_rx + traffic.today_tx
    else:
        traffic.warnings.append(f"⚠️ 未找到今日({today_str})的数据")

    # 昨日数据
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    if yesterday_str in daily_dict:
        traffic.yesterday_rx = daily_dict[yesterday_str]["rx"]
        traffic.yesterday_tx = daily_dict[yesterday_str]["tx"]

    # 本周数据
    weekly_rx = 0
    weekly_tx = 0
    days_counted = 0
    missing_dates = []
    daily_data_list = []

    current = week_start
    while current <= today:
        date_str = current.strftime("%Y-%m-%d")
        weekday_name = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][current.weekday()]

        if date_str in daily_dict:
            rx = daily_dict[date_str]["rx"]
            tx = daily_dict[date_str]["tx"]
            weekly_rx += rx
            weekly_tx += tx
            days_counted += 1
            daily_data_list.append({
                "date": f"{current.strftime('%m-%d')} {weekday_name}",
                "rx": rx,
                "tx": tx,
            })
        else:
            missing_dates.append(f"{date_str} ({weekday_name})")
            daily_data_list.append({
                "date": f"{current.strftime('%m-%d')} {weekday_name}",
                "rx": 0,
                "tx": 0,
            })

        current += timedelta(days=1)

    traffic.weekly_rx = weekly_rx
    traffic.weekly_tx = weekly_tx
    traffic.weekly_total = weekly_rx + weekly_tx
    traffic.days_counted = days_counted
    traffic.missing_dates = missing_dates
    traffic.daily_data = daily_data_list

    # 上周同期数据
    last_week_start = week_start - timedelta(days=7)
    last_week_end = today - timedelta(days=7)
    last_week_rx = 0
    last_week_tx = 0
    current = last_week_start
    while current <= last_week_end:
        date_str = current.strftime("%Y-%m-%d")
        if date_str in daily_dict:
            last_week_rx += daily_dict[date_str]["rx"]
            last_week_tx += daily_dict[date_str]["tx"]
        current += timedelta(days=1)

    traffic.last_week_rx = last_week_rx
    traffic.last_week_tx = last_week_tx

    if missing_dates:
        traffic.warnings.append(f"⚠️ 数据不完整：缺少 {len(missing_dates)} 天的数据")

    return traffic


# ---------------------------------------------------------------------------
# 趋势图
# ---------------------------------------------------------------------------

def generate_progress_bar(current: int, total: int, width: int = 20) -> str:
    """生成 Unicode 进度条。"""
    if total == 0:
        return "░" * width + " 0%"
    filled = int(width * current / total)
    empty = width - filled
    percent = (current / total) * 100
    bar = "█" * filled + "░" * empty
    return f"{bar} {percent:.0f}%"


def generate_ascii_chart(daily_data: list[dict]) -> str:
    """生成 ASCII 文本趋势图。"""
    if not daily_data:
        return "暂无数据"

    max_value = max(max(d.get("rx", 0), d.get("tx", 0)) for d in daily_data)
    if max_value == 0:
        return "暂无数据"

    chart_width = 25
    lines = []
    lines.append("📊 流量趋势图")
    lines.append("─" * 45)

    for d in daily_data:
        date_str = d.get("date", "未知")
        rx = d.get("rx", 0)
        tx = d.get("tx", 0)

        rx_bar_len = int((rx / max_value) * chart_width)
        tx_bar_len = int((tx / max_value) * chart_width)

        rx_bar = "█" * rx_bar_len + "░" * (chart_width - rx_bar_len)
        tx_bar = "█" * tx_bar_len + "░" * (chart_width - tx_bar_len)

        lines.append(f"{date_str} RX [{rx_bar}] {format_bytes(rx)}")
        lines.append(f"{'':>5} TX [{tx_bar}] {format_bytes(tx)}")
        lines.append("")

    return "\n".join(lines)


def generate_trend_chart_image(daily_data: list[dict],
                               save_path: str = None) -> str | None:
    """生成流量趋势图图片，返回保存路径。"""
    if not MATPLOTLIB_AVAILABLE:
        return None

    if not daily_data:
        return None

    save_path = save_path or CHART_SAVE_PATH

    try:
        dates = [d.get("date", "") for d in daily_data]
        rx_values = [d.get("rx", 0) / (1024**3) for d in daily_data]
        tx_values = [d.get("tx", 0) / (1024**3) for d in daily_data]

        plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'SimHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(dates, rx_values, 'b-o', label='入站 (RX)', linewidth=2, markersize=8)
        ax.plot(dates, tx_values, 'r-s', label='出站 (TX)', linewidth=2, markersize=8)
        ax.fill_between(dates, rx_values, alpha=0.3, color='blue')
        ax.fill_between(dates, tx_values, alpha=0.3, color='red')

        ax.set_xlabel('日期', fontsize=12)
        ax.set_ylabel('流量 (GB)', fontsize=12)
        ax.set_title('本周流量趋势', fontsize=14, fontweight='bold')
        ax.legend(fontsize=10, loc='upper left')
        ax.grid(True, linestyle='--', alpha=0.7)
        ax.set_facecolor('#f8f9fa')
        fig.patch.set_facecolor('#ffffff')

        plt.tight_layout()
        plt.savefig(save_path, dpi=150, bbox_inches='tight', facecolor='#ffffff')
        plt.close()

        return save_path
    except Exception as e:
        print(f"[警告] 生成趋势图失败: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# 消息构建
# ---------------------------------------------------------------------------

def build_dingtalk_message(traffic: TrafficData) -> str:
    """构建钉钉精简版消息。"""
    today = datetime.now()
    week_info = get_iso_week_info()

    today_rx_growth = calculate_growth_rate(traffic.today_rx, traffic.yesterday_rx)
    today_tx_growth = calculate_growth_rate(traffic.today_tx, traffic.yesterday_tx)
    today_total_growth = calculate_growth_rate(
        traffic.today_rx + traffic.today_tx,
        traffic.yesterday_rx + traffic.yesterday_tx,
    )

    weekly_rx_growth = calculate_growth_rate(traffic.weekly_rx, traffic.last_week_rx)
    weekly_tx_growth = calculate_growth_rate(traffic.weekly_tx, traffic.last_week_tx)
    weekly_total_growth = calculate_growth_rate(
        traffic.weekly_total,
        traffic.last_week_rx + traffic.last_week_tx,
    )

    progress_bar = generate_progress_bar(traffic.days_counted, 7)

    message = f"""### 📊 服务器流量日报

---

**🖥️ 服务器**: {SERVER_ALIAS} | **IP**: {get_server_ip()}

---

**📅 今日统计（{today.strftime('%Y-%m-%d')} {["周一","周二","周三","周四","周五","周六","周日"][today.weekday()]}）**
| 类型 | 流量 | 较昨日 |
|:---:|:---:|:---:|
| ⬇️ 入站 (RX) | **{format_bytes(traffic.today_rx)}** | {format_growth_rate(today_rx_growth)} |
| ⬆️ 出站 (TX) | **{format_bytes(traffic.today_tx)}** | {format_growth_rate(today_tx_growth)} |
| 📊 总流量 | **{format_bytes(traffic.today_total)}** | {format_growth_rate(today_total_growth)} |

---

**📈 本周统计（第{week_info['week']}周：{week_info['week_start_str']} ~ {week_info['week_end_str']}）**
| 类型 | 流量 | 较上周同期 |
|:---:|:---:|:---:|
| ⬇️ 入站 (RX) | **{format_bytes(traffic.weekly_rx)}** | {format_growth_rate(weekly_rx_growth)} |
| ⬆️ 出站 (TX) | **{format_bytes(traffic.weekly_tx)}** | {format_growth_rate(weekly_tx_growth)} |
| 📊 总流量 | **{format_bytes(traffic.weekly_total)}** | {format_growth_rate(weekly_total_growth)} |

---

**📊 数据完整性**: {traffic.days_counted} / 7 天
{progress_bar}
"""

    if traffic.warnings:
        message += "\n---\n\n**⚠️ 注意事项**\n"
        for warning in traffic.warnings:
            message += f"- {warning}\n"

    message += f"""
---

> 📅 统计时间：{today.strftime('%Y-%m-%d %H:%M:%S')}
> 💡 详细周报将于每周日发送至邮箱

---
*本消息由自动化监控脚本发送*"""

    return message


def build_email_html(traffic: TrafficData, chart_path: str = None) -> str:
    """构建邮件 HTML 内容。"""
    today = datetime.now()
    week_info = get_iso_week_info()

    today_rx_growth = calculate_growth_rate(traffic.today_rx, traffic.yesterday_rx)
    today_tx_growth = calculate_growth_rate(traffic.today_tx, traffic.yesterday_tx)
    today_total_growth = calculate_growth_rate(
        traffic.today_rx + traffic.today_tx,
        traffic.yesterday_rx + traffic.yesterday_tx,
    )
    weekly_rx_growth = calculate_growth_rate(traffic.weekly_rx, traffic.last_week_rx)
    weekly_tx_growth = calculate_growth_rate(traffic.weekly_tx, traffic.last_week_tx)
    weekly_total_growth = calculate_growth_rate(
        traffic.weekly_total,
        traffic.last_week_rx + traffic.last_week_tx,
    )

    progress_percent = (traffic.days_counted / 7) * 100

    daily_rows = ""
    for d in traffic.daily_data:
        daily_rows += f"""
        <tr>
            <td>{d['date']}</td>
            <td>{format_bytes(d['rx'])}</td>
            <td>{format_bytes(d['tx'])}</td>
            <td>{format_bytes(d['rx'] + d['tx'])}</td>
        </tr>"""

    warnings_html = ""
    if traffic.warnings:
        warnings_html = """
        <div class="card warning">
            <h3>⚠️ 注意事项</h3>
            <ul>"""
        for w in traffic.warnings:
            warnings_html += f"<li>{w}</li>"
        warnings_html += "</ul></div>"

    ascii_chart = generate_ascii_chart(traffic.daily_data)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: 'Microsoft YaHei', 'PingFang SC', Arial, sans-serif; margin: 0; padding: 0; background-color: #f5f5f5; }}
        .container {{ max-width: 700px; margin: 0 auto; background-color: #ffffff; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 24px; }}
        .header p {{ margin: 10px 0 0 0; opacity: 0.9; }}
        .content {{ padding: 20px; }}
        .card {{ background: #ffffff; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); border: 1px solid #e0e0e0; }}
        .card.warning {{ border-left: 4px solid #ff9800; }}
        .card h3 {{ color: #333; margin-top: 0; border-bottom: 2px solid #667eea; padding-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
        th {{ background: #667eea; color: white; padding: 12px; text-align: center; }}
        td {{ padding: 10px; text-align: center; border-bottom: 1px solid #eee; }}
        tr:hover {{ background: #f5f5f5; }}
        .trend-up {{ color: #4CAF50; font-weight: bold; }}
        .trend-down {{ color: #f44336; font-weight: bold; }}
        .progress-container {{ background: #e0e0e0; border-radius: 10px; height: 25px; overflow: hidden; margin: 10px 0; }}
        .progress-bar {{ background: linear-gradient(90deg, #4CAF50, #8BC34A); height: 100%; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; }}
        .chart-container {{ background: #f8f9fa; padding: 15px; border-radius: 6px; font-family: monospace; white-space: pre; line-height: 1.4; overflow-x: auto; }}
        .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; background: #f8f9fa; }}
        .server-info {{ display: flex; justify-content: space-around; flex-wrap: wrap; }}
        .server-info-item {{ text-align: center; padding: 10px; }}
        .server-info-value {{ font-weight: bold; color: #333; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 服务器流量周报</h1>
            <p>第{week_info['week']}周 ({week_info['week_start'].strftime('%Y-%m-%d')} ~ {week_info['week_end'].strftime('%Y-%m-%d')})</p>
        </div>

        <div class="content">
            <div class="card">
                <h3>🖥️ 服务器信息</h3>
                <div class="server-info">
                    <div class="server-info-item">
                        <div>服务器</div>
                        <div class="server-info-value">{SERVER_ALIAS}</div>
                    </div>
                    <div class="server-info-item">
                        <div>IP 地址</div>
                        <div class="server-info-value">{get_server_ip()}</div>
                    </div>
                    <div class="server-info-item">
                        <div>网卡</div>
                        <div class="server-info-value">{INTERFACE}</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <h3>📅 今日统计（{today.strftime('%Y-%m-%d')} {["周一","周二","周三","周四","周五","周六","周日"][today.weekday()]}）</h3>
                <table>
                    <tr><th>类型</th><th>流量</th><th>较昨日</th></tr>
                    <tr><td>⬇️ 入站 (RX)</td><td><strong>{format_bytes(traffic.today_rx)}</strong></td>
                        <td class="{'trend-up' if today_rx_growth and today_rx_growth >= 0 else 'trend-down'}">{format_growth_rate(today_rx_growth)}</td></tr>
                    <tr><td>⬆️ 出站 (TX)</td><td><strong>{format_bytes(traffic.today_tx)}</strong></td>
                        <td class="{'trend-up' if today_tx_growth and today_tx_growth >= 0 else 'trend-down'}">{format_growth_rate(today_tx_growth)}</td></tr>
                    <tr><td>📊 总流量</td><td><strong>{format_bytes(traffic.today_total)}</strong></td>
                        <td class="{'trend-up' if today_total_growth and today_total_growth >= 0 else 'trend-down'}">{format_growth_rate(today_total_growth)}</td></tr>
                </table>
            </div>

            <div class="card">
                <h3>📈 本周流量汇总（第{week_info['week']}周）</h3>
                <table>
                    <tr><th>类型</th><th>流量</th><th>较上周同期</th></tr>
                    <tr><td>⬇️ 入站 (RX)</td><td><strong>{format_bytes(traffic.weekly_rx)}</strong></td>
                        <td class="{'trend-up' if weekly_rx_growth and weekly_rx_growth >= 0 else 'trend-down'}">{format_growth_rate(weekly_rx_growth)}</td></tr>
                    <tr><td>⬆️ 出站 (TX)</td><td><strong>{format_bytes(traffic.weekly_tx)}</strong></td>
                        <td class="{'trend-up' if weekly_tx_growth and weekly_tx_growth >= 0 else 'trend-down'}">{format_growth_rate(weekly_tx_growth)}</td></tr>
                    <tr><td>📊 总流量</td><td><strong>{format_bytes(traffic.weekly_total)}</strong></td>
                        <td class="{'trend-up' if weekly_total_growth and weekly_total_growth >= 0 else 'trend-down'}">{format_growth_rate(weekly_total_growth)}</td></tr>
                </table>
            </div>

            <div class="card">
                <h3>📊 数据完整性</h3>
                <p>已统计 <strong>{traffic.days_counted} / 7 天</strong></p>
                <div class="progress-container">
                    <div class="progress-bar" style="width: {progress_percent}%">{progress_percent:.0f}%</div>
                </div>
            </div>

            <div class="card">
                <h3>📅 每日流量明细</h3>
                <table>
                    <tr><th>日期</th><th>入站 (RX)</th><th>出站 (TX)</th><th>总流量</th></tr>
                    {daily_rows}
                </table>
            </div>

            <div class="card">
                <h3>📈 流量趋势</h3>
                {"<img src='cid:trend_chart' alt='流量趋势图' style='width: 100%; border-radius: 8px;'>" if chart_path else f'<div class="chart-container">{ascii_chart}</div>'}
            </div>

            {warnings_html}
        </div>

        <div class="footer">
            <p>📅 生成时间：{today.strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>💡 本邮件由自动化监控脚本发送</p>
        </div>
    </div>
</body>
</html>"""

    return html


def should_send_weekly_report() -> bool:
    """判断是否应该发送周报。"""
    return datetime.now().weekday() == WEEKLY_REPORT_DAY
