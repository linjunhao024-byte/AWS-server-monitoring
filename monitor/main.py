#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AWS Lightsail 服务器监控系统 v3.0

终端菜单入口，整合所有功能：
- 查看监控状态
- 带宽积分分析
- 流量报表
- 路由状态
- 配置管理
- 服务管理
"""

import os
import sys
import subprocess

# 确保能找到同目录下的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    INTERFACE, DATA_DIR, INSTALL_DIR,
    DINGTALK_WEBHOOK, DINGTALK_SECRET,
    XFYUN_API_KEY, XFYUN_ENABLED,
    EMAIL_ENABLED, SMTP_USERNAME, EMAIL_RECIPIENTS,
    SERVER_ALIAS, ROUTE_TARGET, ROUTE_INTERVAL,
    save_config,
)
from utils import now_iso, format_bytes, get_server_ip


# ---------------------------------------------------------------------------
# 颜色常量
# ---------------------------------------------------------------------------
CYAN   = '\033[0;36m'
GREEN  = '\033[0;32m'
YELLOW = '\033[1;33m'
RED    = '\033[0;31m'
BOLD   = '\033[1m'
DIM    = '\033[2m'
NC     = '\033[0m'

# 标准宽度：内容区 73 字符
W = 73  # 内容区宽度


# ---------------------------------------------------------------------------
# 基础 UI 工具
# ---------------------------------------------------------------------------

def clear_screen():
    """清屏。"""
    os.system('cls' if os.name == 'nt' else 'clear')


def pause():
    """等待用户按键继续。"""
    print()
    input(f"  {DIM}按 Enter 继续...{NC}")


def c(color: str, text: str) -> str:
    """给文字加色。"""
    return f"{color}{text}{NC}"


# ---------------------------------------------------------------------------
# 框线绘制
# ---------------------------------------------------------------------------

def main_frame(title: str):
    """绘制主菜单框架标题（双线框）。"""
    print()
    print(c(CYAN, f"╔{'═' * W}╗"))
    print(c(CYAN, "║") + c(BOLD, f"  {title:<{W-2}}") + c(CYAN, "║"))
    print(c(CYAN, f"╠{'═' * W}╣"))


def main_row(text: str = ""):
    """主框架内容行。"""
    print(c(CYAN, "║") + f"  {text:<{W-2}}" + c(CYAN, "║"))


def main_row_c(text: str = ""):
    """主框架居中内容行。"""
    print(c(CYAN, "║") + f"{text:^{W}}" + c(CYAN, "║"))


def main_sep():
    """主框架分隔线。"""
    print(c(CYAN, f"╠{'═' * W}╣"))


def main_end():
    """主框架结束线。"""
    print(c(CYAN, f"╚{'═' * W}╝"))
    print()


def _display_width(s: str) -> int:
    """计算字符串的终端显示宽度（中文占 2 列）。"""
    import unicodedata
    width = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ('F', 'W'):
            width += 2
        else:
            width += 1
    return width


def _strip_ansi(s: str) -> str:
    """去除 ANSI 转义序列。"""
    import re
    return re.sub(r'\033\[[0-9;]*m', '', s)


def step_frame(title: str):
    """绘制步骤框标题（单线框）。"""
    print()
    print(c(CYAN, f"┌{'─' * W}┐"))
    raw = f"  {title}"
    pad = W - _display_width(raw)
    if pad < 0:
        pad = 0
    print(c(CYAN, "│") + c(BOLD, raw) + " " * pad + c(CYAN, "│"))
    print(c(CYAN, f"├{'─' * W}┤"))


def step_row(text: str = ""):
    """步骤框内容行。"""
    visible = _strip_ansi(text)
    pad = W - 4 - _display_width(visible)
    if pad < 0:
        pad = 0
    print(c(CYAN, "│") + f"  {text}" + " " * pad + c(CYAN, "│"))


def step_sep():
    """步骤框内分隔线。"""
    print(c(CYAN, f"├{'─' * W}┤"))


def step_end():
    """步骤框结束线。"""
    print(c(CYAN, f"└{'─' * W}┘"))
    print()


# ---------------------------------------------------------------------------
# 选项输入
# ---------------------------------------------------------------------------

def ask_choice(prompt: str, options: list[str]) -> int:
    """显示选项列表，返回用户选择的索引（-1 表示返回）。"""
    step_sep()
    for i, opt in enumerate(options, 1):
        step_row(f"  {c(YELLOW, str(i))}. {opt}")
    step_row()
    step_row(f"  {c(DIM, '0. 返回')}")
    step_end()

    while True:
        try:
            choice = int(input(f"  {c(YELLOW, prompt)} [0-{len(options)}]: "))
            if choice == 0:
                return -1
            if 1 <= choice <= len(options):
                return choice - 1
        except ValueError:
            pass
        print(f"  {c(RED, '无效输入，请重试')}")


def _systemctl_status(service: str) -> str:
    """获取 systemd 服务状态。"""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "inactive"
    except Exception:
        return "未知"


def _mask(s: str) -> str:
    """脱敏显示。"""
    if not s:
        return "(未配置)"
    if len(s) <= 8:
        return "****"
    return s[:4] + "****" + s[-4:]


# ---------------------------------------------------------------------------
# 1. 查看监控状态
# ---------------------------------------------------------------------------

def menu_status():
    """查看监控状态。"""
    clear_screen()

    # 服务状态
    step_frame("📊  监控状态")
    step_row(c(BOLD, "服务状态"))
    step_sep()

    services = [
        ("bandwidth-monitor", "带宽采集"),
        ("route-monitor", "路由监测"),
        ("bandwidth-analyzer.timer", "每日分析"),
    ]
    for svc, desc in services:
        status = _systemctl_status(svc)
        if "active" in status:
            icon = c(GREEN, "●")
            st = c(GREEN, status)
        else:
            icon = c(RED, "●")
            st = c(RED, status)
        step_row(f"  {icon}  {desc:<16} {st}")

    # 最新数据文件
    step_sep()
    step_row(c(BOLD, "最新数据"))
    step_sep()

    from analyzer import find_latest_file, find_files_in_dir
    import datetime

    latest = find_latest_file()
    if latest:
        mtime = os.path.getmtime(latest)
        dt = datetime.datetime.fromtimestamp(mtime)
        size = os.path.getsize(latest)
        step_row(f"  文件:  {c(GREEN, os.path.basename(latest))}")
        step_row(f"  时间:  {dt.strftime('%Y-%m-%d %H:%M:%S')}")
        step_row(f"  大小:  {c(GREEN, format_bytes(size))}")
    else:
        step_row(f"  {c(YELLOW, '⚠ 未找到数据文件')}")

    all_files = find_files_in_dir()
    step_row(f"  总计:  {len(all_files)} 个文件")

    # 路由日志
    step_sep()
    step_row(c(BOLD, "路由监测"))
    step_sep()

    if os.path.exists(DATA_DIR):
        route_files = sorted([
            f for f in os.listdir(DATA_DIR) if f.startswith("route_log_")
        ])
        if route_files:
            latest_route = os.path.join(DATA_DIR, route_files[-1])
            try:
                with open(latest_route, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    for line in lines[-4:]:
                        step_row(f"  {c(DIM, line.rstrip()[:W-4])}")
            except Exception:
                step_row(f"  最新: {c(GREEN, route_files[-1])}")
        else:
            step_row(f"  {c(YELLOW, '⚠ 未找到路由日志')}")
    else:
        step_row(f"  {c(YELLOW, '⚠ 数据目录不存在')}")

    step_end()
    pause()


# ---------------------------------------------------------------------------
# 2. 带宽积分分析
# ---------------------------------------------------------------------------

def menu_analyzer():
    """带宽积分分析子菜单。"""
    clear_screen()
    step_frame("🔍  带宽积分分析")

    options = [
        "分析最新数据",
        "选择文件分析",
        "多天数据对比分析",
    ]
    idx = ask_choice("选择分析方式", options)
    if idx == -1:
        return

    from analyzer import (
        find_latest_file, find_files_in_dir, load_csv_files,
        reverse_engineer_credits, generate_report,
        build_llm_prompt,
    )
    from stats import basic_stats
    from notifications import send_dingtalk, call_xfyun

    if idx == 0:
        latest = find_latest_file()
        if not latest:
            print(f"\n  {c(YELLOW, '⚠ 未找到数据文件')}")
            pause()
            return
        file_paths = [latest]
    elif idx == 1:
        all_files = find_files_in_dir()
        if not all_files:
            print(f"\n  {c(YELLOW, '⚠ 未找到数据文件')}")
            pause()
            return
        print()
        for i, f in enumerate(all_files, 1):
            print(f"  {c(YELLOW, str(i))}. {os.path.basename(f)}")
        try:
            choice = int(input(f"\n  {c(YELLOW, '输入文件编号')}: ")) - 1
            if 0 <= choice < len(all_files):
                file_paths = [all_files[choice]]
            else:
                print(f"  {c(RED, '无效选择')}")
                pause()
                return
        except ValueError:
            print(f"  {c(RED, '无效输入')}")
            pause()
            return
    else:
        all_files = find_files_in_dir()
        if len(all_files) < 2:
            print(f"\n  {c(YELLOW, '⚠ 需要至少 2 个文件才能对比')}")
            pause()
            return
        file_paths = all_files

    # 执行分析
    step_frame("分析进度")
    step_row(f"  加载 {len(file_paths)} 个文件...")
    rows = load_csv_files(file_paths)
    if not rows:
        step_row(c(RED, "  ✗ 没有有效数据可分析"))
        step_end()
        pause()
        return
    step_row(f"  {c(GREEN, '✓')} 共加载 {len(rows):,} 条记录")
    step_row(f"  运行积分机制反推算法...")
    analysis = reverse_engineer_credits(rows)
    step_row(f"  {c(GREEN, '✓')} 分析完成")
    step_end()

    report = generate_report(rows, analysis, file_paths)

    # LLM 分析
    if XFYUN_ENABLED:
        step_frame("LLM 分析")
        step_row(f"  调用讯飞星火进行深度分析...")
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
            report += "\n---\n\n## 🤖 讯飞星火深度分析\n\n"
            report += llm_result + "\n"
            step_row(f"  {c(GREEN, '✓')} 讯飞分析完成")
        else:
            step_row(f"  {c(YELLOW, '⚠')} 讯飞分析失败")
        step_end()

    # 输出报告
    print(f"\n{c(CYAN, '═' * W)}")
    print(report)
    print(c(CYAN, '═' * W))

    # 推送到钉钉
    if DINGTALK_WEBHOOK:
        push = input(f"\n  {c(YELLOW, '是否推送到钉钉？(y/N)')}: ").strip().lower()
        if push == 'y':
            send_dingtalk("🔍 带宽积分机制分析报告", report)

    pause()


# ---------------------------------------------------------------------------
# 3. 流量报表
# ---------------------------------------------------------------------------

def menu_reporter():
    """流量报子菜单。"""
    clear_screen()
    step_frame("📈  流量报表")

    options = [
        "查看今日流量",
        "发送钉钉日报",
        "发送邮件周报",
    ]
    idx = ask_choice("选择操作", options)
    if idx == -1:
        return

    from reporter import get_traffic_data, build_dingtalk_message, build_email_html
    from reporter import generate_trend_chart_image
    from notifications import send_dingtalk, send_email

    step_frame("数据获取")
    step_row(f"  获取流量数据...")
    traffic = get_traffic_data()
    if not traffic:
        step_row(c(RED, "  ✗ 无法获取流量数据"))
        step_row(f"  {c(DIM, '请检查 vnstat 是否安装并运行')}")
        step_end()
        pause()
        return
    step_row(f"  {c(GREEN, '✓')} 数据获取成功")
    step_end()

    if idx == 0:
        step_frame("📊  今日流量概览")
        step_row()
        step_row(f"  {'入站 (RX):':<16} {c(GREEN, format_bytes(traffic.today_rx))}")
        step_row(f"  {'出站 (TX):':<16} {c(GREEN, format_bytes(traffic.today_tx))}")
        step_row(f"  {'总流量:':<16} {c(GREEN, format_bytes(traffic.today_total))}")
        step_sep()
        step_row(c(BOLD, "  本周流量"))
        step_sep()
        step_row(f"  {'入站 (RX):':<16} {c(GREEN, format_bytes(traffic.weekly_rx))}")
        step_row(f"  {'出站 (TX):':<16} {c(GREEN, format_bytes(traffic.weekly_tx))}")
        step_row(f"  {'总流量:':<16} {c(GREEN, format_bytes(traffic.weekly_total))}")
        step_row(f"  {'数据完整性:':<16} {traffic.days_counted}/7 天")
        step_row()
        step_end()

    elif idx == 1:
        msg = build_dingtalk_message(traffic)
        step_frame("📨  钉钉日报预览")
        for line in msg.split('\n')[:15]:
            step_row(f"  {c(DIM, line[:W-4])}")
        step_row(f"  {c(DIM, '...')}")
        step_end()

        if DINGTALK_WEBHOOK:
            send_dingtalk("📊 服务器流量日报", msg)
            print(f"  {c(GREEN, '✓')} 钉钉日报发送成功")
        else:
            print(f"  {c(YELLOW, '⚠ 钉钉未配置')}")

    elif idx == 2:
        if not EMAIL_ENABLED:
            print(f"\n  {c(YELLOW, '⚠ 邮件功能未启用')}")
            pause()
            return
        step_frame("📧  邮件周报")
        step_row(f"  生成周报...")
        chart_path = generate_trend_chart_image(traffic.daily_data)
        html = build_email_html(traffic, chart_path)
        from utils import get_iso_week_info
        week_info = get_iso_week_info()
        subject = f"📊 服务器流量周报 - 第{week_info['week']}周 - {SERVER_ALIAS}"
        send_email(subject, html, chart_path)
        if chart_path and os.path.exists(chart_path):
            os.remove(chart_path)
        step_end()

    pause()


# ---------------------------------------------------------------------------
# 4. 路由状态
# ---------------------------------------------------------------------------

def menu_route():
    """路由状态查看。"""
    clear_screen()
    step_frame("📡  路由状态")

    step_row(f"  目标:  {c(GREEN, ROUTE_TARGET)}")
    step_row(f"  间隔:  {ROUTE_INTERVAL} 秒")
    step_sep()

    if os.path.exists(DATA_DIR):
        route_files = sorted([
            f for f in os.listdir(DATA_DIR) if f.startswith("route_log_")
        ])
        if route_files:
            latest = os.path.join(DATA_DIR, route_files[-1])
            step_row(c(BOLD, f"  最新日志: {route_files[-1]}"))
            step_sep()
            try:
                with open(latest, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    for line in lines[-8:]:
                        step_row(f"  {c(DIM, line.rstrip()[:W-4])}")
            except Exception as e:
                step_row(f"  {c(RED, f'读取失败: {e}')}")
        else:
            step_row(f"  {c(YELLOW, '⚠ 未找到路由日志')}")
    else:
        step_row(f"  {c(YELLOW, '⚠ 数据目录不存在')}")

    step_sep()
    step_row(f"  {c(DIM, 'sudo systemctl status route-monitor')}")
    step_end()
    pause()


# ---------------------------------------------------------------------------
# 5. 配置管理
# ---------------------------------------------------------------------------

def menu_config():
    """配置管理子菜单。"""
    clear_screen()
    step_frame("⚙️  配置管理")

    step_row(c(BOLD, "  当前配置"))
    step_sep()

    # 服务器
    step_row(f"  服务器别名   {c(GREEN, SERVER_ALIAS)}")
    step_row(f"  网卡         {c(GREEN, INTERFACE)}")
    step_row(f"  数据目录     {c(DIM, DATA_DIR)}")

    step_sep()

    # 钉钉
    webhook_disp = c(GREEN, _mask(DINGTALK_WEBHOOK)) if DINGTALK_WEBHOOK else c(DIM, "(未配置)")
    secret_disp = c(GREEN, _mask(DINGTALK_SECRET)) if DINGTALK_SECRET else c(DIM, "(未配置)")
    step_row(f"  钉钉 Webhook {webhook_disp}")
    step_row(f"  钉钉 Secret  {secret_disp}")

    step_sep()

    # LLM
    xfyun_disp = c(GREEN, _mask(XFYUN_API_KEY)) if XFYUN_API_KEY else c(DIM, "(未配置)")
    xfyun_st = c(GREEN, "已启用") if XFYUN_ENABLED else c(DIM, "已禁用")
    step_row(f"  讯飞 API Key {xfyun_disp}")
    step_row(f"  讯飞状态     {xfyun_st}")

    step_sep()

    # 邮件
    email_st = c(GREEN, "已启用") if EMAIL_ENABLED else c(DIM, "已禁用")
    smtp_disp = SMTP_USERNAME or c(DIM, "(未配置)")
    recv_disp = ', '.join(EMAIL_RECIPIENTS) if EMAIL_RECIPIENTS else c(DIM, "(未配置)")
    step_row(f"  邮件状态     {email_st}")
    step_row(f"  SMTP 用户    {smtp_disp}")
    step_row(f"  收件人       {recv_disp}")

    step_sep()

    # 路由
    step_row(f"  路由目标     {c(GREEN, ROUTE_TARGET)}")
    step_row(f"  路由间隔     {ROUTE_INTERVAL} 秒")

    options = [
        "修改服务器别名",
        "配置钉钉 Webhook",
        "配置讯飞星火 LLM",
        "配置邮件",
        "修改路由监测参数",
    ]
    idx = ask_choice("选择操作", options)
    if idx == -1:
        return

    import config as cfg

    if idx == 0:
        alias = input(f"\n  服务器别名 [{c(GREEN, SERVER_ALIAS)}]: ").strip()
        if alias:
            cfg.SERVER_ALIAS = alias
            print(f"  {c(GREEN, '✓')} 已设置: {alias}")

    elif idx == 1:
        webhook = input(f"\n  钉钉 Webhook URL: ").strip()
        secret = input(f"  钉钉 Secret (SEC开头): ").strip()
        if webhook:
            cfg.DINGTALK_WEBHOOK = webhook
            print(f"  {c(GREEN, '✓')} Webhook 已设置")
        if secret:
            cfg.DINGTALK_SECRET = secret
            print(f"  {c(GREEN, '✓')} Secret 已设置")

    elif idx == 2:
        api_key = input(f"\n  讯飞 API Key: ").strip()
        if api_key:
            cfg.XFYUN_API_KEY = api_key
            cfg.XFYUN_ENABLED = True
            print(f"  {c(GREEN, '✓')} 讯飞星火已启用")
        else:
            cfg.XFYUN_API_KEY = ""
            cfg.XFYUN_ENABLED = False
            print(f"  {c(GREEN, '✓')} 讯飞星火已禁用")

    elif idx == 3:
        enable = input(f"\n  启用邮件功能？(y/n): ").strip().lower()
        if enable == 'y':
            cfg.EMAIL_ENABLED = True
            username = input(f"  SMTP 用户名 [{c(GREEN, SMTP_USERNAME or '')}]: ").strip()
            if username:
                cfg.SMTP_USERNAME = username
            password = input(f"  SMTP 密码: ").strip()
            if password:
                cfg.SMTP_PASSWORD = password
            recipients = input(f"  收件人（逗号分隔）: ").strip()
            if recipients:
                cfg.EMAIL_RECIPIENTS = [r.strip() for r in recipients.split(",")]
            print(f"  {c(GREEN, '✓')} 邮件已启用")
        else:
            cfg.EMAIL_ENABLED = False
            print(f"  {c(GREEN, '✓')} 邮件已禁用")

    elif idx == 4:
        target = input(f"\n  路由目标 [{c(GREEN, ROUTE_TARGET)}]: ").strip()
        if target:
            cfg.ROUTE_TARGET = target
        interval = input(f"  检测间隔(秒) [{ROUTE_INTERVAL}]: ").strip()
        if interval:
            try:
                cfg.ROUTE_INTERVAL = int(interval)
            except ValueError:
                print(f"  {c(YELLOW, '⚠ 无效数字，保持原值')}")
        print(f"  {c(GREEN, '✓')} 路由配置已更新")

    save_config()
    print(f"\n  {c(GREEN, '✓')} 配置已保存到 settings.json")
    pause()


# ---------------------------------------------------------------------------
# 6. 服务管理
# ---------------------------------------------------------------------------

def menu_service():
    """服务管理子菜单。"""
    clear_screen()
    step_frame("🔧  服务管理")

    step_row(c(BOLD, "  服务状态"))
    step_sep()

    services = [
        ("bandwidth-monitor", "带宽采集"),
        ("route-monitor", "路由监测"),
        ("bandwidth-analyzer.timer", "每日分析定时器"),
    ]
    for svc, desc in services:
        status = _systemctl_status(svc)
        if "active" in status:
            icon = c(GREEN, "●")
            st = c(GREEN, status)
        else:
            icon = c(RED, "●")
            st = c(RED, status)
        step_row(f"  {icon}  {desc:<16} {st}")

    options = [
        "启动带宽采集",
        "停止带宽采集",
        "重启带宽采集",
        "启动路由监测",
        "停止路由监测",
        "查看服务日志",
    ]
    idx = ask_choice("选择操作", options)
    if idx == -1:
        return

    commands = {
        0: ("start", "bandwidth-monitor"),
        1: ("stop", "bandwidth-monitor"),
        2: ("restart", "bandwidth-monitor"),
        3: ("start", "route-monitor"),
        4: ("stop", "route-monitor"),
    }

    if idx < 5:
        action, svc = commands[idx]
        result = subprocess.run(
            ["systemctl", action, svc],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"\n  {c(GREEN, '✓')} {action} {svc} 成功")
        else:
            print(f"\n  {c(RED, '✗')} 失败: {result.stderr.strip()}")
    else:
        print()
        log_options = ["bandwidth-monitor", "route-monitor", "bandwidth-analyzer"]
        log_idx = ask_choice("选择服务", log_options)
        if log_idx >= 0:
            svc = log_options[log_idx]
            print(f"\n  {c(CYAN, f'--- {svc} 最近日志 ---')}")
            subprocess.run(
                ["journalctl", "-u", svc, "-n", "30", "--no-pager"],
                timeout=10,
            )

    pause()


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    """主循环。"""
    while True:
        clear_screen()

        # 主标题
        main_frame("AWS Lightsail 服务器监控系统 v3.0")
        main_row(f"服务器: {c(GREEN, SERVER_ALIAS)}")
        main_row(f"IP:     {c(GREEN, get_server_ip())}")
        main_sep()
        main_row(f"  {c(YELLOW, '1')}. 📊  查看监控状态")
        main_row(f"  {c(YELLOW, '2')}. 🔍  带宽积分分析")
        main_row(f"  {c(YELLOW, '3')}. 📈  流量报表")
        main_row(f"  {c(YELLOW, '4')}. 📡  路由状态")
        main_row(f"  {c(YELLOW, '5')}. ⚙️   配置管理")
        main_row(f"  {c(YELLOW, '6')}. 🔧  服务管理")
        main_row()
        main_row(f"  {c(DIM, '0')}. {c(DIM, '退出')}")
        main_end()

        try:
            choice = input(f"  {c(YELLOW, '请选择')} [0-6]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n  {c(GREEN, '👋 再见！')}")
            break

        if choice == "0":
            print(f"\n  {c(GREEN, '👋 再见！')}")
            break
        elif choice == "1":
            menu_status()
        elif choice == "2":
            menu_analyzer()
        elif choice == "3":
            menu_reporter()
        elif choice == "4":
            menu_route()
        elif choice == "5":
            menu_config()
        elif choice == "6":
            menu_service()
        else:
            print(f"  {c(RED, '无效输入，请重试')}")
            pause()


if __name__ == "__main__":
    main()
