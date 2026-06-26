#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AWS Lightsail 服务器监控系统 v3.0

终端菜单入口，整合所有功能。
"""

import os
import sys
import time
import threading
import subprocess

# 确保能找到同目录下的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    INTERFACE, DATA_DIR, INSTALL_DIR,
    DINGTALK_WEBHOOK, DINGTALK_SECRET,
    TG_BOT_TOKEN, TG_CHAT_ID,
    XFYUN_API_KEY, XFYUN_ENABLED,
    WEEKLY_REPORT_DAY, SENDER_NAME, DAILY_REPORT_TIME,
    EMAIL_ENABLED, SMTP_SERVER, SMTP_PORT, SMTP_USE_SSL,
    SMTP_USERNAME, SMTP_PASSWORD, EMAIL_RECIPIENTS,
    SERVER_ALIAS, ROUTE_TARGET, ROUTE_INTERVAL,
    LOG_RETENTION_DAYS, DISK_ALERT_MB, CURRENT_VERSION,
    ROUTE_ALERT_ENABLED,
    save_config,
)
from utils import (
    now_iso, format_bytes, get_server_ip,
    health_check, rotate_logs, check_disk_alert, check_version, do_update,
)


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

W = 73  # 内容区宽度


# ---------------------------------------------------------------------------
# 基础 UI 工具
# ---------------------------------------------------------------------------

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def pause():
    print()
    input(f"  {DIM}按 Enter 继续...{NC}")


def c(color: str, text: str) -> str:
    return f"{color}{text}{NC}"


def _display_width(s: str) -> int:
    import unicodedata
    width = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ('F', 'W'):
            width += 2
        else:
            width += 1
    return width


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r'\033\[[0-9;]*m', '', s)


def _pad(text: str, width: int) -> str:
    """补空格到指定显示宽度。"""
    visible = _strip_ansi(text)
    dw = _display_width(visible)
    pad = width - dw
    return text + " " * max(0, pad)


# ---------------------------------------------------------------------------
# 框线绘制
# ---------------------------------------------------------------------------

# 菜单总宽度（内容区，不含两侧 │）
MW = 73

def _cyan(s: str) -> str:
    return c(CYAN, s)

def _hline(ch: str = "=") -> str:
    return _cyan(ch * MW)

def _hline_split(ch1: str = "-", pos: int = 36, ch2: str = "-") -> str:
    return _cyan(ch1 * pos + ch2 * (MW - pos))

def _full_row(text: str) -> str:
    dw = _display_width(_strip_ansi(text))
    return f"  {_cyan('|')}{text}{' ' * max(0, MW - dw)}{_cyan('|')}"

def _2col_row(left: str, right: str, split: int = 36) -> str:
    l = _pad(left, split)
    r = _pad(right, MW - split)
    return f"  {_cyan('|')}{l}{_cyan('|')}{r}{_cyan('|')}"

def _3col_row(c1: str, c2: str, c3: str) -> str:
    w = 23  # 23*3 + 2 separators = 71, pad to 73
    p1 = _pad(c1, w + 1)
    p2 = _pad(c2, w + 1)
    p3 = _pad(c3, w)
    return f"  {_cyan('|')}{p1}{_cyan('|')}{p2}{_cyan('|')}{p3}{_cyan('|')}"


def step_frame(title: str):
    print()
    print(c(CYAN, f"┌{'─' * W}┐"))
    raw = f"  {title}"
    pad = W - _display_width(raw)
    print(c(CYAN, "│") + c(BOLD, raw) + " " * max(0, pad) + c(CYAN, "│"))
    print(c(CYAN, f"├{'─' * W}┤"))


def step_row(text: str = ""):
    visible = _strip_ansi(text)
    pad = W - 4 - _display_width(visible)
    print(c(CYAN, "│") + f"  {text}" + " " * max(0, pad) + c(CYAN, "│"))


def step_sep():
    print(c(CYAN, f"├{'─' * W}┤"))


def step_end():
    print(c(CYAN, f"└{'─' * W}┘"))
    print()


def ask_choice(prompt: str, options: list[str]) -> int:
    step_sep()
    for i, opt in enumerate(options, 1):
        step_row(f"  {c(YELLOW, str(i))}. {opt}")
    step_row()
    step_row(f"  {c(DIM, '0. 返回')}")
    step_end()
    while True:
        try:
            raw = input(f"  {c(YELLOW, prompt)} [0-{len(options)}]: ").strip()
            if not raw:
                return -1  # 空输入 = 返回
            choice = int(raw)
            if choice == 0:
                return -1
            if 1 <= choice <= len(options):
                return choice - 1
        except ValueError:
            pass
        print(f"  {c(RED, '无效输入，请重试')}")


def _systemctl_status(service: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "inactive"
    except Exception:
        return "未知"


def _update_daily_report_timer(time_str: str) -> bool:
    """更新每日报告的 systemd timer 时间。"""
    try:
        hh, mm = time_str.split(":")
        hh = int(hh)
        mm = int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return False
    except (ValueError, AttributeError):
        return False

    timer_content = f"""[Unit]
Description=Send daily traffic report at {time_str}

[Timer]
OnCalendar=*-*-* {hh}:{mm:02d}:00
Persistent=true

[Install]
WantedBy=timers.target
"""

    timer_path = "/etc/systemd/system/bandwidth-daily-report.timer"
    try:
        with open(timer_path, "w") as f:
            f.write(timer_content)
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=10)
        subprocess.run(["systemctl", "re-enable", "bandwidth-daily-report.timer"],
                       capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def _mask(s: str) -> str:
    if not s:
        return "(未配置)"
    if len(s) <= 8:
        return "****"
    return s[:4] + "****" + s[-4:]


# ---------------------------------------------------------------------------
# 功能函数
# ---------------------------------------------------------------------------

def action_status():
    """[1] 查看状态"""
    clear_screen()
    step_frame("监控状态")
    step_row(c(BOLD, "  健康自检"))
    step_sep()
    results = health_check()
    for r in results:
        if r["status"] == "ok":
            icon = c(GREEN, "✓")
        elif r["status"] == "warn":
            icon = c(YELLOW, "⚠")
        else:
            icon = c(RED, "✗")
        step_row(f"  {icon}  {r['component']:<12} {r['message']}")

    step_sep()
    step_row(c(BOLD, "  最新数据"))
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
        step_row(f"  {c(YELLOW, '未找到数据文件')}")
    all_files = find_files_in_dir()
    step_row(f"  总计:  {len(all_files)} 个文件")

    step_sep()
    step_row(c(BOLD, "  路由监测"))
    step_sep()
    if os.path.exists(DATA_DIR):
        route_files = sorted([f for f in os.listdir(DATA_DIR) if f.startswith("route_log_")])
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
            step_row(f"  {c(YELLOW, '未找到路由日志')}")
    else:
        step_row(f"  {c(YELLOW, '数据目录不存在')}")
    step_end()
    pause()


def action_start():
    """[2] 启动服务"""
    clear_screen()
    step_frame("启动服务")
    options = ["启动带宽采集", "启动路由监测", "全部启动"]
    idx = ask_choice("选择服务", options)
    if idx == -1:
        return
    svcs = [("bandwidth-monitor", "带宽采集"), ("route-monitor", "路由监测")] if idx == 2 else \
           [("bandwidth-monitor", "带宽采集")] if idx == 0 else [("route-monitor", "路由监测")]
    step_frame("执行结果")
    for svc, desc in svcs:
        r = subprocess.run(["systemctl", "start", svc], capture_output=True, timeout=10)
        if r.returncode == 0:
            step_row(f"  {c(GREEN, '✓')} {desc} 已启动")
        else:
            step_row(f"  {c(RED, '✗')} {desc} 启动失败")
    step_end()
    pause()


def action_stop():
    """[3] 停止服务"""
    clear_screen()
    step_frame("停止服务")
    options = ["停止带宽采集", "停止路由监测", "全部停止"]
    idx = ask_choice("选择服务", options)
    if idx == -1:
        return
    svcs = [("bandwidth-monitor", "带宽采集"), ("route-monitor", "路由监测")] if idx == 2 else \
           [("bandwidth-monitor", "带宽采集")] if idx == 0 else [("route-monitor", "路由监测")]
    step_frame("执行结果")
    for svc, desc in svcs:
        r = subprocess.run(["systemctl", "stop", svc], capture_output=True, timeout=10)
        if r.returncode == 0:
            step_row(f"  {c(GREEN, '✓')} {desc} 已停止")
        else:
            step_row(f"  {c(RED, '✗')} {desc} 停止失败")
    step_end()
    pause()


def action_restart():
    """[4] 重启服务"""
    clear_screen()
    step_frame("重启服务")
    options = ["重启带宽采集", "重启路由监测", "全部重启"]
    idx = ask_choice("选择服务", options)
    if idx == -1:
        return
    svcs = [("bandwidth-monitor", "带宽采集"), ("route-monitor", "路由监测")] if idx == 2 else \
           [("bandwidth-monitor", "带宽采集")] if idx == 0 else [("route-monitor", "路由监测")]
    step_frame("执行结果")
    for svc, desc in svcs:
        r = subprocess.run(["systemctl", "restart", svc], capture_output=True, timeout=10)
        if r.returncode == 0:
            step_row(f"  {c(GREEN, '✓')} {desc} 已重启")
        else:
            step_row(f"  {c(RED, '✗')} {desc} 重启失败")
    step_end()
    pause()


def action_realtime_log():
    """[5] 实时日志"""
    clear_screen()
    step_frame("实时日志")
    options = ["带宽采集日志", "路由监测日志"]
    idx = ask_choice("选择服务", options)
    if idx == -1:
        return
    svc = "bandwidth-monitor" if idx == 0 else "route-monitor"
    print(f"\n  {c(DIM, '按 Enter 停止实时日志并返回菜单')}\n")
    proc = subprocess.Popen(
        ["journalctl", "-u", svc, "-f", "--no-pager"],
        stdout=sys.stdout, stderr=sys.stderr,
    )
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def action_recent_log():
    """[6] 最近日志"""
    clear_screen()
    step_frame("最近日志")
    options = ["带宽采集日志", "路由监测日志", "每日分析日志"]
    idx = ask_choice("选择服务", options)
    if idx == -1:
        return
    svcs = ["bandwidth-monitor", "route-monitor", "bandwidth-analyzer"]
    svc = svcs[idx]
    print(f"\n  {c(CYAN, f'--- {svc} 最近 30 行 ---')}\n")
    subprocess.run(["journalctl", "-u", svc, "-n", "30", "--no-pager"], timeout=10)
    print(f"\n  {c(DIM, '按 Enter 返回菜单')}")
    pause()


def action_manual_push():
    """[7] 手动推送"""
    clear_screen()
    step_frame("手动推送")
    options = [
        "发送钉钉测试消息",
        "发送 Telegram 测试消息",
        "发送钉钉+Telegram 日报",
        "发送邮件周报",
        "发送 Telegram 菜单键盘",
    ]
    idx = ask_choice("选择操作", options)
    if idx == -1:
        return

    from notifications import send_dingtalk, send_telegram, send_tg_with_menu, send_email, _server_info_block

    if idx == 0:
        if not DINGTALK_WEBHOOK:
            print(f"\n  {c(YELLOW, '钉钉未配置')}")
            pause()
            return
        msg = f"### 钉钉连接测试\n\n{_server_info_block()}\n- **时间**: {now_iso()}\n\n> 测试成功\n\n---\n*菜单手动测试*"
        ok = send_dingtalk("钉钉连接测试", msg)
        print(f"\n  {c(GREEN, '✓') if ok else c(RED, '✗')} {'发送成功' if ok else '发送失败'}")

    elif idx == 1:
        if not TG_BOT_TOKEN:
            print(f"\n  {c(YELLOW, 'Telegram 未配置')}")
            pause()
            return
        msg = f"🔔 *Telegram 连接测试*\n\n{_server_info_block()}\n\n_测试成功_"
        ok = send_telegram(msg)
        print(f"\n  {c(GREEN, '✓') if ok else c(RED, '✗')} {'发送成功' if ok else '发送失败'}")

    elif idx == 2:
        from reporter import get_traffic_data, build_dingtalk_message
        traffic = get_traffic_data()
        if traffic:
            msg = build_dingtalk_message(traffic)
            if DINGTALK_WEBHOOK:
                send_dingtalk("服务器流量日报", msg)
                print(f"\n  {c(GREEN, '✓')} 钉钉日报已发送")
            if TG_BOT_TOKEN:
                tg_msg = f"📊 *服务器流量日报*\n\n{_server_info_block()}\n\n入站: {format_bytes(traffic.today_rx)} | 出站: {format_bytes(traffic.today_tx)} | 总计: {format_bytes(traffic.today_total)}"
                send_telegram(tg_msg)
                print(f"  {c(GREEN, '✓')} Telegram 日报已发送")
        else:
            print(f"\n  {c(YELLOW, '无法获取流量数据')}")

    elif idx == 3:
        if not EMAIL_ENABLED:
            print(f"\n  {c(YELLOW, '邮件功能未启用')}")
            pause()
            return
        from reporter import get_traffic_data, build_email_html, generate_trend_chart_image
        from utils import get_iso_week_info
        traffic = get_traffic_data()
        if traffic:
            chart_path = generate_trend_chart_image(traffic.daily_data)
            html = build_email_html(traffic, chart_path)
            week_info = get_iso_week_info()
            subject = f"服务器流量周报 - 第{week_info['week']}周 - {SERVER_ALIAS}"
            send_email(subject, html, chart_path)
            if chart_path and os.path.exists(chart_path):
                os.remove(chart_path)
            print(f"\n  {c(GREEN, '✓')} 周报已发送")
        else:
            print(f"\n  {c(YELLOW, '无法获取流量数据')}")

    elif idx == 4:
        if not TG_BOT_TOKEN:
            print(f"\n  {c(YELLOW, 'Telegram 未配置')}")
            pause()
            return
        ok = send_tg_with_menu(f"🖥️ *{SERVER_ALIAS}*\n\n选择操作:")
        print(f"\n  {c(GREEN, '✓') if ok else c(RED, '✗')} {'菜单已发送' if ok else '发送失败'}")

    pause()


def action_view_config():
    """[8] 查看配置"""
    clear_screen()
    step_frame("当前配置")
    step_row(f"  服务器别名   {c(GREEN, SERVER_ALIAS)}")
    step_row(f"  网卡         {c(GREEN, INTERFACE)}")
    step_row(f"  数据目录     {c(DIM, DATA_DIR)}")
    step_sep()
    step_row(c(BOLD, "  通知通道"))
    step_sep()
    webhook_disp = c(GREEN, _mask(DINGTALK_WEBHOOK)) if DINGTALK_WEBHOOK else c(DIM, "(未配置)")
    step_row(f"  钉钉         {webhook_disp}")
    tg_disp = c(GREEN, _mask(TG_BOT_TOKEN)) if TG_BOT_TOKEN else c(DIM, "(未配置)")
    step_row(f"  Telegram     {tg_disp}")
    if TG_CHAT_ID:
        step_row(f"  TG Chat ID   {c(GREEN, TG_CHAT_ID)}")
    xfyun_st = c(GREEN, "已启用") if XFYUN_ENABLED else c(DIM, "已禁用")
    step_row(f"  讯飞星火     {xfyun_st}")
    email_st = c(GREEN, "已启用") if EMAIL_ENABLED else c(DIM, "已禁用")
    step_row(f"  邮件         {email_st}")
    step_sep()
    step_row(c(BOLD, "  监控参数"))
    step_sep()
    step_row(f"  路由目标     {c(GREEN, ROUTE_TARGET)}")
    step_row(f"  路由间隔     {ROUTE_INTERVAL} 秒")
    step_row(f"  路由告警     {'已开启' if ROUTE_ALERT_ENABLED else c(DIM, '已关闭')}")
    step_row(f"  日志保留     {LOG_RETENTION_DAYS} 天")
    step_row(f"  磁盘告警     {DISK_ALERT_MB} MB")
    step_sep()
    step_row(c(BOLD, "  报表设置"))
    step_sep()
    step_row(f"  每日推送     {c(GREEN, DAILY_REPORT_TIME)}")
    week_days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    step_row(f"  周报发送日   每{week_days[WEEKLY_REPORT_DAY]}")
    step_row(f"  发件人       {SENDER_NAME}")
    step_end()
    pause()


def _config_display():
    """编辑配置的显示面板。"""
    wh_st = c(GREEN, _mask(DINGTALK_WEBHOOK)) if DINGTALK_WEBHOOK else c(DIM, "(未配置)")
    tg_st = c(GREEN, _mask(TG_BOT_TOKEN)) if TG_BOT_TOKEN else c(DIM, "(未配置)")
    xf_st = c(GREEN, "已启用") if XFYUN_ENABLED else c(DIM, "已禁用")
    em_st = c(GREEN, "已启用") if EMAIL_ENABLED else c(DIM, "已禁用")

    print()
    print(f"  {_cyan('+')}{_hline('-')}{_cyan('+')}")
    print(_full_row(f"  {BOLD}编辑配置{NC}"))
    print(f"  {_cyan('+')}{_hline('-')}{_cyan('+')}")
    print(_full_row(""))
    print(_2col_row(f"  {c(YELLOW, '[1]')}  服务器名称  {c(GREEN, SERVER_ALIAS)}", f"  {c(YELLOW, '[5]')}  钉钉配置    {wh_st}"))
    print(_2col_row(f"  {c(YELLOW, '[2]')}  网卡        {c(GREEN, INTERFACE)}", f"  {c(YELLOW, '[6]')}  Telegram    {tg_st}"))
    print(_2col_row(f"  {c(YELLOW, '[3]')}  路由目标    {c(GREEN, ROUTE_TARGET)}", f"  {c(YELLOW, '[7]')}  邮件配置    {em_st}"))
    print(_2col_row(f"  {c(YELLOW, '[4]')}  检测间隔    {ROUTE_INTERVAL}秒", f"  {c(YELLOW, '[8]')}  讯飞配置    {xf_st}"))
    print(_full_row(""))
    print(f"  {_cyan('+')}{_hline('=')}{_cyan('+')}")
    print(_full_row(f"  {c(YELLOW, '[9]')}  运维参数"))
    print(f"  {_cyan('+')}{_hline('=')}{_cyan('+')}")
    print(_full_row(f"  {c(DIM, '[0]')}  {c(DIM, '返回')}"))
    print(f"  {_cyan('+')}{_hline('=')}{_cyan('+')}")


def action_edit_config():
    """[9] 编辑配置"""
    import config as cfg

    while True:
        clear_screen()
        _config_display()

        try:
            ch = input(f"\n  {c(YELLOW, '请选择')} [0-9]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return

        if ch == "0" or not ch:
            return

        if ch == "1":
            clear_screen()
            step_frame("服务器名称")
            step_row(f"  当前: {c(GREEN, SERVER_ALIAS)}")
            step_end()
            alias = input(f"  输入新名称: ").strip()
            if alias:
                cfg.SERVER_ALIAS = alias
                save_config()
                print(f"  {c(GREEN, '✓')} 已保存")
                time.sleep(0.8)

        elif ch == "2":
            clear_screen()
            step_frame("网卡设置")
            step_row(f"  当前: {c(GREEN, INTERFACE)}")
            step_sep()
            from data_sources import list_physical_interfaces
            ifaces = list_physical_interfaces()
            for iface in ifaces:
                marker = c(GREEN, " <-- 当前") if iface == INTERFACE else ""
                step_row(f"  {iface}{marker}")
            step_end()
            iface = input(f"  输入网卡名称: ").strip()
            if iface:
                cfg.INTERFACE = iface
                save_config()
                print(f"  {c(GREEN, '✓')} 已保存")
                time.sleep(0.8)

        elif ch == "3":
            clear_screen()
            step_frame("路由目标")
            step_row(f"  当前: {c(GREEN, ROUTE_TARGET)}")
            step_end()
            t = input(f"  输入新目标: ").strip()
            if t:
                cfg.ROUTE_TARGET = t
                save_config()
                print(f"  {c(GREEN, '✓')} 已保存")
                time.sleep(0.8)

        elif ch == "4":
            clear_screen()
            step_frame("检测间隔")
            step_row(f"  当前: {ROUTE_INTERVAL} 秒")
            step_end()
            i = input(f"  输入新间隔(秒): ").strip()
            if i:
                try:
                    cfg.ROUTE_INTERVAL = int(i)
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
                except ValueError:
                    print(f"  {c(RED, '无效数字')}")

        elif ch == "5":
            clear_screen()
            step_frame("钉钉配置")
            wh_st = c(GREEN, _mask(DINGTALK_WEBHOOK)) if DINGTALK_WEBHOOK else c(DIM, "(未配置)")
            sec_st = c(GREEN, _mask(DINGTALK_SECRET)) if DINGTALK_SECRET else c(DIM, "(未配置)")
            step_row(f"  Webhook: {wh_st}")
            step_row(f"  Secret:  {sec_st}")
            step_sep()
            step_row(f"  {c(YELLOW, '[1]')} 修改 Webhook")
            step_row(f"  {c(YELLOW, '[2]')} 修改 Secret")
            step_row(f"  {c(YELLOW, '[3]')} 清除")
            step_end()
            sub = input(f"  选择: ").strip()
            if sub == "1":
                wh = input(f"  Webhook URL: ").strip()
                if wh:
                    cfg.DINGTALK_WEBHOOK = wh
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
            elif sub == "2":
                sec = input(f"  Secret: ").strip()
                if sec:
                    cfg.DINGTALK_SECRET = sec
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
            elif sub == "3":
                cfg.DINGTALK_WEBHOOK = ""
                cfg.DINGTALK_SECRET = ""
                save_config()
                print(f"  {c(GREEN, '✓')} 已清除")
                time.sleep(0.8)

        elif ch == "6":
            clear_screen()
            step_frame("Telegram 配置")
            tg_st = c(GREEN, _mask(TG_BOT_TOKEN)) if TG_BOT_TOKEN else c(DIM, "(未配置)")
            step_row(f"  Bot Token: {tg_st}")
            step_row(f"  Chat ID:   {TG_CHAT_ID or c(DIM, '(未配置)')}")
            step_sep()
            step_row(f"  {c(YELLOW, '[1]')} 修改 Bot Token")
            step_row(f"  {c(YELLOW, '[2]')} 修改 Chat ID")
            step_row(f"  {c(YELLOW, '[3]')} 清除配置")
            step_row(f"  {c(YELLOW, '[4]')} 发送测试消息")
            step_end()
            sub = input(f"  选择: ").strip()
            if sub == "1":
                token = input(f"  Bot Token: ").strip()
                if token:
                    cfg.TG_BOT_TOKEN = token
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
                    time.sleep(0.8)
            elif sub == "2":
                chat_id = input(f"  Chat ID: ").strip()
                if chat_id:
                    cfg.TG_CHAT_ID = chat_id
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
                    time.sleep(0.8)
            elif sub == "3":
                cfg.TG_BOT_TOKEN = ""
                cfg.TG_CHAT_ID = ""
                save_config()
                print(f"  {c(GREEN, '✓')} 已清除")
                time.sleep(0.8)
            elif sub == "4":
                from notifications import send_telegram, _server_info_block
                ok = send_telegram(f"🔔 *Telegram 连接测试*\n\n{_server_info_block()}\n\n_测试成功_")
                if ok:
                    print(f"  {c(GREEN, '✓')} 测试消息已发送")
                else:
                    print(f"  {c(RED, '✗')} 发送失败，请检查 Token 和 Chat ID")
                time.sleep(0.8)

        elif ch == "7":
            clear_screen()
            step_frame("邮件配置")
            em_st = c(GREEN, "已启用") if EMAIL_ENABLED else c(DIM, "已禁用")
            step_row(f"  状态:     {em_st}")
            step_row(f"  SMTP:     {SMTP_SERVER}:{SMTP_PORT} {'(SSL)' if SMTP_USE_SSL else ''}")
            step_row(f"  用户:     {SMTP_USERNAME or c(DIM, '(未配置)')}")
            step_row(f"  收件人:   {', '.join(EMAIL_RECIPIENTS) if EMAIL_RECIPIENTS else c(DIM, '(未配置)')}")
            step_sep()
            step_row(f"  {c(YELLOW, '[1]')} 开关邮件")
            step_row(f"  {c(YELLOW, '[2]')} SMTP 服务器/端口/SSL")
            step_row(f"  {c(YELLOW, '[3]')} SMTP 用户名")
            step_row(f"  {c(YELLOW, '[4]')} SMTP 密码")
            step_row(f"  {c(YELLOW, '[5]')} 收件人")
            step_row(f"  {c(YELLOW, '[6]')} 发件人名称 ({SENDER_NAME})")
            step_row(f"  {c(YELLOW, '[7]')} 周报发送日 ({['周一','周二','周三','周四','周五','周六','周日'][WEEKLY_REPORT_DAY]})")
            step_end()
            sub = input(f"  选择: ").strip()
            if sub == "1":
                cfg.EMAIL_ENABLED = not cfg.EMAIL_ENABLED
                save_config()
                st = "已启用" if cfg.EMAIL_ENABLED else "已禁用"
                print(f"  {c(GREEN, '✓')} 邮件 {st}")
            elif sub == "2":
                srv = input(f"  SMTP 服务器 [{SMTP_SERVER}]: ").strip()
                if srv: cfg.SMTP_SERVER = srv
                prt = input(f"  SMTP 端口 [{SMTP_PORT}]: ").strip()
                if prt:
                    try: cfg.SMTP_PORT = int(prt)
                    except ValueError: pass
                ssl = input(f"  使用 SSL？(Y/n): ").strip().lower()
                cfg.SMTP_USE_SSL = ssl != 'n'
                save_config()
                print(f"  {c(GREEN, '✓')} 已保存")
                time.sleep(0.8)
            elif sub == "3":
                u = input(f"  SMTP 用户名: ").strip()
                if u:
                    cfg.SMTP_USERNAME = u
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
            elif sub == "4":
                p = input(f"  SMTP 密码: ").strip()
                if p:
                    cfg.SMTP_PASSWORD = p
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
            elif sub == "5":
                r = input(f"  收件人（逗号分隔）: ").strip()
                if r:
                    cfg.EMAIL_RECIPIENTS = [x.strip() for x in r.split(",") if x.strip()]
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
            elif sub == "6":
                name = input(f"  发件人名称 [{SENDER_NAME}]: ").strip()
                if name:
                    cfg.SENDER_NAME = name
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
            elif sub == "7":
                days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                for i, d in enumerate(days):
                    marker = c(GREEN, " <-- 当前") if i == WEEKLY_REPORT_DAY else ""
                    print(f"  {c(YELLOW, str(i))}. {d}{marker}")
                try:
                    ch_day = int(input(f"  选择 [0-6]: ").strip())
                    if 0 <= ch_day <= 6:
                        cfg.WEEKLY_REPORT_DAY = ch_day
                        save_config()
                        print(f"  {c(GREEN, '✓')} 周报将在每周{days[ch_day]}发送")
                except ValueError:
                    pass

        elif ch == "8":
            clear_screen()
            step_frame("讯飞配置")
            xf_st = c(GREEN, "已启用") if XFYUN_ENABLED else c(DIM, "已禁用")
            step_row(f"  状态:     {xf_st}")
            step_row(f"  Key:      {c(GREEN, _mask(XFYUN_API_KEY)) if XFYUN_API_KEY else c(DIM, '(未配置)')}")
            step_row(f"  模型:     {XFYUN_MODEL}")
            step_sep()
            step_row(f"  {c(YELLOW, '[1]')} 修改 API Key")
            step_row(f"  {c(YELLOW, '[2]')} 修改模型名称")
            step_row(f"  {c(YELLOW, '[3]')} 禁用")
            step_end()
            sub = input(f"  选择: ").strip()
            if sub == "1":
                key = input(f"  API Key: ").strip()
                if key:
                    cfg.XFYUN_API_KEY = key
                    cfg.XFYUN_ENABLED = True
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
            elif sub == "2":
                m = input(f"  模型名称 [{XFYUN_MODEL}]: ").strip()
                if m:
                    cfg.XFYUN_MODEL = m
                    save_config()
                    print(f"  {c(GREEN, '✓')} 已保存")
            elif sub == "3":
                cfg.XFYUN_API_KEY = ""
                cfg.XFYUN_ENABLED = False
                save_config()
                print(f"  {c(GREEN, '✓')} 已禁用")
                time.sleep(0.8)

        elif ch == "9":
            clear_screen()
            step_frame("运维参数")
            step_row(f"  日志保留:  {LOG_RETENTION_DAYS} 天")
            step_row(f"  磁盘告警:  {DISK_ALERT_MB} MB")
            step_sep()
            step_row(f"  {c(YELLOW, '[1]')} 修改日志保留天数")
            step_row(f"  {c(YELLOW, '[2]')} 修改磁盘告警阈值")
            step_end()
            sub = input(f"  选择: ").strip()
            if sub == "1":
                d = input(f"  日志保留天数 [{LOG_RETENTION_DAYS}]: ").strip()
                if d:
                    try:
                        cfg.LOG_RETENTION_DAYS = int(d)
                        save_config()
                        print(f"  {c(GREEN, '✓')} 已保存")
                    except ValueError:
                        print(f"  {c(RED, '无效数字')}")
            elif sub == "2":
                m = input(f"  磁盘告警阈值(MB) [{DISK_ALERT_MB}]: ").strip()
                if m:
                    try:
                        cfg.DISK_ALERT_MB = int(m)
                        save_config()
                        print(f"  {c(GREEN, '✓')} 已保存")
                    except ValueError:
                        print(f"  {c(RED, '无效数字')}")

        else:
            print(f"  {c(RED, '无效输入')}")
            pause()


def action_boot_toggle():
    """[10] 开机自启"""
    clear_screen()
    step_frame("开机自启管理")
    services = [
        ("bandwidth-monitor.service", "带宽采集"),
        ("route-monitor.service", "路由监测"),
        ("bandwidth-analyzer.timer", "每日分析"),
        ("bandwidth-daily-report.timer", "每日推送"),
        ("bandwidth-maintenance.timer", "日志轮转"),
    ]
    step_row(c(BOLD, "  当前状态"))
    step_sep()
    for svc, desc in services:
        status = _systemctl_status(svc)
        if "active" in status:
            icon = c(GREEN, "●")
        else:
            icon = c(RED, "●")
        enabled = subprocess.run(["systemctl", "is-enabled", svc],
                                 capture_output=True, text=True, timeout=5)
        en = enabled.stdout.strip()
        en_disp = c(GREEN, "已启用") if en == "enabled" else c(DIM, "已禁用")
        step_row(f"  {icon}  {desc:<16} {en_disp}")

    step_sep()
    options = ["全部启用开机自启", "全部禁用开机自启"]
    idx = ask_choice("选择操作", options)
    if idx == -1:
        return
    action = "enable" if idx == 0 else "disable"
    for svc, desc in services:
        subprocess.run(["systemctl", action, svc], capture_output=True, timeout=5)
    print(f"\n  {c(GREEN, '✓')} 已{'启用' if idx == 0 else '禁用'}全部开机自启")
    pause()


def action_nic_info():
    """[11] 网卡与下载"""
    clear_screen()
    step_frame("网卡与下载")
    step_row(c(BOLD, "  网卡信息"))
    step_sep()
    step_row(f"  当前网卡:  {c(GREEN, INTERFACE)}")
    try:
        rx = open(f"/sys/class/net/{INTERFACE}/statistics/rx_bytes").read().strip()
        tx = open(f"/sys/class/net/{INTERFACE}/statistics/tx_bytes").read().strip()
        step_row(f"  RX 总计:   {c(GREEN, format_bytes(int(rx)))}")
        step_row(f"  TX 总计:   {c(GREEN, format_bytes(int(tx)))}")
    except Exception:
        step_row(f"  {c(YELLOW, '无法读取网卡数据')}")

    step_sep()
    step_row(c(BOLD, "  可用网卡"))
    step_sep()
    from data_sources import list_physical_interfaces
    ifaces = list_physical_interfaces()
    for iface in ifaces:
        marker = c(GREEN, " <-- 当前") if iface == INTERFACE else ""
        step_row(f"  {iface}{marker}")

    step_sep()
    step_row(c(BOLD, "  下载链接"))
    step_sep()
    step_row(f"  {c(DIM, 'wget https://github.com/linjunhao024-byte/')}")
    step_row(f"  {c(DIM, 'AWS-server-monitoring/archive/refs/heads/')}")
    step_row(f"  {c(DIM, 'main.tar.gz')}")
    step_end()
    pause()


def action_uninstall():
    """[12] 卸载"""
    clear_screen()
    step_frame("卸载")
    step_row(f"  {c(RED, '此操作将删除所有监控文件和服务')}")
    step_sep()
    if input(f"  {c(RED, '确认卸载？(y/N)')} ").strip().lower() != 'y':
        print(f"\n  已取消")
        pause()
        return

    services = ["bandwidth-monitor.service", "route-monitor.service",
                "bandwidth-analyzer.service", "bandwidth-analyzer.timer",
                "bandwidth-daily-report.service", "bandwidth-daily-report.timer",
                "bandwidth-maintenance.service", "bandwidth-maintenance.timer",
                "bandwidth-data-check.service", "bandwidth-data-check.timer"]
    for svc in services:
        subprocess.run(["systemctl", "stop", svc], capture_output=True, timeout=5)
        subprocess.run(["systemctl", "disable", svc], capture_output=True, timeout=5)
        # 删除 service 文件
        svc_path = f"/etc/systemd/system/{svc}"
        if os.path.exists(svc_path):
            os.remove(svc_path)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=5)

    import shutil
    for f in ["/usr/local/bin/monitor"]:
        if os.path.exists(f):
            os.remove(f)
    if os.path.exists(INSTALL_DIR):
        shutil.rmtree(INSTALL_DIR)

    print(f"\n  {c(GREEN, '✓')} 卸载完成")
    print(f"  {c(DIM, '数据目录已保留，如需删除: rm -rf ' + DATA_DIR)}")
    pause()


def action_update():
    """[13] 一键更新"""
    clear_screen()
    step_frame("一键更新")
    step_row(f"  当前版本: {c(GREEN, CURRENT_VERSION)}")
    step_row(f"  检查 GitHub...")
    step_sep()
    ver = check_version()
    step_row(f"  GitHub 版本: {c(GREEN, ver['latest'])}")
    if ver.get("error"):
        step_row(f"  {c(RED, '网络错误: ' + ver['error'])}")
    if not ver["update_available"]:
        step_row(f"  {c(GREEN, '✓ 已是最新版本')}")
        step_end()
        pause()
        return
    step_row(f"  {c(YELLOW, '发现新版本')} {c(GREEN, ver['latest'])}")
    step_end()

    if input(f"  确认更新？(y/N): ").strip().lower() != 'y':
        return

    step_frame("更新进度")
    step_row(f"  下载中...")
    result = do_update()
    if result["success"]:
        step_row(f"  {c(GREEN, '✓')} {result['message']}")
    else:
        step_row(f"  {c(RED, '✗')} {result['message']}")
    step_end()

    if result["success"]:
        print(f"\n  {c(YELLOW, '按回车将重启服务并重新进入面板')}")
        input()
        os.execv(sys.executable, [sys.executable] + sys.argv)
    pause()


def _get_current_shortcuts() -> list[str]:
    """获取当前所有指向 monitor 的快捷命令。"""
    shortcuts = []
    bin_dir = "/usr/local/bin"
    monitor_path = os.path.join(bin_dir, "monitor")
    if not os.path.exists(monitor_path):
        return shortcuts
    for name in os.listdir(bin_dir):
        path = os.path.join(bin_dir, name)
        if os.path.islink(path) and os.readlink(path) == monitor_path:
            shortcuts.append(name)
        elif os.path.isfile(path) and path != monitor_path:
            try:
                with open(path, "r") as f:
                    if "main.py" in f.read():
                        shortcuts.append(name)
            except Exception:
                pass
    return sorted(set(shortcuts))


def action_auto_panel():
    """[14] 自动面板 & 快捷命令"""
    clear_screen()
    step_frame("自动面板 & 快捷命令")

    # 自动面板状态
    bashrc = os.path.expanduser("~/.bashrc")
    marker = "# AWS-Monitor-AutoPanel"
    is_enabled = False
    if os.path.exists(bashrc):
        with open(bashrc, "r") as f:
            is_enabled = marker in f.read()

    auto_st = c(GREEN, "已开启") if is_enabled else c(DIM, "已关闭")
    step_row(f"  SSH 登录自动面板: {auto_st}")
    step_sep()

    # 快捷命令状态
    shortcuts = _get_current_shortcuts()
    if shortcuts:
        shortcut_str = ", ".join([c(GREEN, s) for s in shortcuts])
        step_row(f"  可用命令: {shortcut_str}")
    else:
        step_row(f"  可用命令: {c(DIM, '未设置')}")
    step_row(f"  {c(DIM, '输入命令名即可进入菜单面板')}")
    step_sep()

    options = [
        "开启自动面板",
        "关闭自动面板",
        "添加快捷命令",
        "删除快捷命令",
    ]
    idx = ask_choice("选择操作", options)
    if idx == -1:
        return

    if idx == 0:
        if is_enabled:
            print(f"\n  {c(YELLOW, '已经是开启状态')}")
        else:
            with open(bashrc, "a") as f:
                f.write(f"\n{marker}\nmonitor\n")
            print(f"\n  {c(GREEN, '✓')} 已开启，下次 SSH 登录自动进入面板")

    elif idx == 1:
        if not is_enabled:
            print(f"\n  {c(YELLOW, '已经是关闭状态')}")
        else:
            with open(bashrc, "r") as f:
                lines = f.readlines()
            with open(bashrc, "w") as f:
                skip = False
                for line in lines:
                    if marker in line:
                        skip = True
                        continue
                    if skip and line.strip() == "monitor":
                        skip = False
                        continue
                    f.write(line)
            print(f"\n  {c(GREEN, '✓')} 已关闭")

    elif idx == 2:
        name = input(f"\n  输入快捷命令名称 (如 m, mo, mt): ").strip()
        if not name:
            return
        if not all(c.isalnum() or c in '_-' for c in name):
            print(f"  {c(RED, '无效名称，只允许字母、数字、下划线、横线')}")
        elif os.path.exists(f"/usr/local/bin/{name}"):
            print(f"  {c(RED, '{name} 已被其他命令占用')}")
        else:
            os.symlink("/usr/local/bin/monitor", f"/usr/local/bin/{name}")
            print(f"  {c(GREEN, '✓')} 已创建: {name} → monitor")

    elif idx == 3:
        if not shortcuts:
            print(f"\n  {c(YELLOW, '没有可删除的快捷命令')}")
        else:
            print()
            for i, s in enumerate(shortcuts, 1):
                print(f"  {c(YELLOW, str(i))}. {s}")
            try:
                ch = int(input(f"\n  选择要删除的编号: ")) - 1
                if 0 <= ch < len(shortcuts):
                    target = shortcuts[ch]
                    if target == "monitor":
                        print(f"  {c(RED, '不能删除主命令 monitor')}")
                    else:
                        os.remove(f"/usr/local/bin/{target}")
                        print(f"  {c(GREEN, '✓')} 已删除: {target}")
            except (ValueError, IndexError):
                pass

    pause()


def action_traffic():
    """[15] 流量与带宽"""
    clear_screen()
    step_frame("流量与带宽")
    from reporter import get_traffic_data
    traffic = get_traffic_data()
    if not traffic:
        step_row(f"  {c(YELLOW, '无法获取流量数据（vnstat 未安装？）')}")
        step_end()
        pause()
        return
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
    step_end()
    pause()


def action_alert_settings():
    """[16] 告警设置"""
    clear_screen()
    step_frame("告警设置")
    step_row(c(BOLD, "  告警通道"))
    step_sep()
    step_row(f"  钉钉告警:    {'已配置' if DINGTALK_WEBHOOK else c(YELLOW, '未配置')}")
    step_row(f"  Telegram:    {'已配置' if TG_BOT_TOKEN else c(YELLOW, '未配置')}")
    step_row(f"  邮件告警:    {'已启用' if EMAIL_ENABLED else c(DIM, '已禁用')}")
    step_row(f"  讯飞 AI:     {'已启用' if XFYUN_ENABLED else c(DIM, '已禁用')}")
    step_sep()
    step_row(c(BOLD, "  告警开关"))
    step_sep()
    rt_st = c(GREEN, "已开启") if ROUTE_ALERT_ENABLED else c(DIM, "已关闭")
    step_row(f"  路由变化告警: {rt_st}")
    step_sep()
    step_row(c(BOLD, "  告警阈值"))
    step_sep()
    step_row(f"  磁盘告警:    {DISK_ALERT_MB} MB")
    step_row(f"  日志保留:    {LOG_RETENTION_DAYS} 天")
    step_sep()
    step_row(c(BOLD, "  报表设置"))
    step_sep()
    week_days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    step_row(f"  每日推送:    {c(GREEN, DAILY_REPORT_TIME)}")
    step_row(f"  周报发送日:  每{week_days[WEEKLY_REPORT_DAY]}")
    step_row(f"  发件人名称:  {SENDER_NAME}")

    options = ["切换路由变化告警", "修改每日推送时间"]
    idx = ask_choice("选择操作", options)
    import config as cfg
    if idx == 0:
        cfg.ROUTE_ALERT_ENABLED = not cfg.ROUTE_ALERT_ENABLED
        save_config()
        st = "已开启" if cfg.ROUTE_ALERT_ENABLED else "已关闭"
        print(f"\n  {c(GREEN, '✓')} 路由变化告警 {st}")
    elif idx == 1:
        new_time = input(f"\n  推送时间 (HH:MM) [{DAILY_REPORT_TIME}]: ").strip()
        if new_time:
            if _update_daily_report_timer(new_time):
                cfg.DAILY_REPORT_TIME = new_time
                save_config()
                print(f"  {c(GREEN, '✓')} 每日推送时间已设为 {new_time}")
            else:
                print(f"  {c(RED, '无效时间格式，请使用 HH:MM')}")
    pause()


def action_ai_analysis():
    """[17] AI分析"""
    clear_screen()
    step_frame("AI分析")
    if not XFYUN_ENABLED:
        step_row(f"  {c(YELLOW, '讯飞星火未配置')}")
        step_row(f"  {c(DIM, '请先在 [9] 编辑配置 中设置 API Key')}")
        step_end()
        pause()
        return

    options = ["分析最新数据", "选择文件分析", "多天数据对比"]
    idx = ask_choice("选择分析方式", options)
    if idx == -1:
        return

    from analyzer import find_latest_file, find_files_in_dir, load_csv_files, reverse_engineer_credits, generate_report, build_llm_prompt
    from stats import basic_stats
    from notifications import call_xfyun

    if idx == 0:
        latest = find_latest_file()
        if not latest:
            print(f"\n  {c(YELLOW, '未找到数据文件')}")
            pause()
            return
        file_paths = [latest]
    elif idx == 1:
        all_files = find_files_in_dir()
        if not all_files:
            print(f"\n  {c(YELLOW, '未找到数据文件')}")
            pause()
            return
        print()
        for i, f in enumerate(all_files, 1):
            print(f"  {c(YELLOW, str(i))}. {os.path.basename(f)}")
        try:
            ch = int(input(f"\n  输入文件编号: ")) - 1
            if 0 <= ch < len(all_files):
                file_paths = [all_files[ch]]
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
            print(f"\n  {c(YELLOW, '需要至少 2 个文件')}")
            pause()
            return
        file_paths = all_files

    step_frame("分析进度")
    step_row(f"  加载 {len(file_paths)} 个文件...")
    rows = load_csv_files(file_paths)
    if not rows:
        step_row(c(RED, "  没有有效数据"))
        step_end()
        pause()
        return
    step_row(f"  {c(GREEN, '✓')} 共 {len(rows):,} 条记录")
    step_row(f"  运行积分反推算法...")
    analysis = reverse_engineer_credits(rows)
    step_row(f"  {c(GREEN, '✓')} 分析完成")
    step_sep()
    step_row(f"  调用讯飞星火...")
    rx_vals = [r["rx_mbps"] for r in rows]
    tx_vals = [r["tx_mbps"] for r in rows]
    cpu_vals = [r["cpu_load_1m"] for r in rows]
    days = (rows[-1]["timestamp"] - rows[0]["timestamp"]).total_seconds() / 86400
    prompt = build_llm_prompt(analysis, basic_stats(rx_vals, "Rx"), basic_stats(tx_vals, "Tx"),
                              basic_stats(cpu_vals, "CPU"), days, len(rows), rows=rows)
    llm_result = call_xfyun(prompt)
    if llm_result:
        step_row(f"  {c(GREEN, '✓')} 讯飞分析完成")
    else:
        step_row(f"  {c(YELLOW, '讯飞分析失败，仅输出统计报告')}")
    step_end()

    report = generate_report(rows, analysis, file_paths)
    if llm_result:
        report += "\n---\n\n## AI 深度分析\n\n" + llm_result + "\n"
    print(f"\n{c(CYAN, '═' * W)}")
    print(report)
    print(c(CYAN, '═' * W))

    if DINGTALK_WEBHOOK:
        push = input(f"\n  {c(YELLOW, '推送到钉钉？(y/N)')}: ").strip().lower()
        if push == 'y':
            from notifications import send_dingtalk
            send_dingtalk("带宽积分分析报告", report)
    pause()


# ---------------------------------------------------------------------------
# Telegram 内联键盘回调处理
# ---------------------------------------------------------------------------

def _handle_tg_callback(callback_data: str, query_id: str) -> tuple[str, list]:
    """
    处理 Telegram 回调，返回 (结果文本, 键盘)。
    """
    from notifications import (
        answer_callback, _server_info_block,
        _tg_main_keyboard, _tg_svc_keyboard, _tg_log_keyboard,
    )
    answer_callback(query_id, "处理中...")

    # ── 主菜单操作 ──
    if callback_data == "status":
        results = health_check()
        lines = ["📊 *监控状态*\n"]
        for r in results:
            icon = {"ok": "✓", "warn": "⚠", "error": "✗"}.get(r["status"], "?")
            lines.append(f"`{icon}` {r['component']}: {r['message']}")
        return "\n".join(lines), _tg_main_keyboard()

    elif callback_data == "traffic":
        from reporter import get_traffic_data
        traffic = get_traffic_data()
        if not traffic:
            return "⚠ 无法获取流量数据", _tg_main_keyboard()
        return (
            f"📈 *今日流量*\n\n"
            f"入站: `{format_bytes(traffic.today_rx)}`\n"
            f"出站: `{format_bytes(traffic.today_tx)}`\n"
            f"总计: `{format_bytes(traffic.today_total)}`\n\n"
            f"*本周*\n"
            f"入站: `{format_bytes(traffic.weekly_rx)}`\n"
            f"出站: `{format_bytes(traffic.weekly_tx)}`\n"
            f"总计: `{format_bytes(traffic.weekly_total)}`\n"
            f"完整性: {traffic.days_counted}/7 天"
        ), _tg_main_keyboard()

    elif callback_data == "route":
        if os.path.exists(DATA_DIR):
            route_files = sorted([f for f in os.listdir(DATA_DIR) if f.startswith("route_log_")])
            if route_files:
                latest = os.path.join(DATA_DIR, route_files[-1])
                try:
                    with open(latest, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        last_entries = [l.rstrip() for l in lines[-8:] if l.strip()]
                        return "📡 *路由状态*\n\n" + "\n".join(last_entries), _tg_main_keyboard()
                except Exception:
                    pass
        return "⚠ 未找到路由日志", _tg_main_keyboard()

    elif callback_data == "analyze":
        from analyzer import find_latest_file, load_csv_files, reverse_engineer_credits
        latest = find_latest_file()
        if not latest:
            return "⚠ 未找到数据文件", _tg_main_keyboard()
        rows = load_csv_files([latest])
        if not rows:
            return "⚠ 没有有效数据", _tg_main_keyboard()
        analysis = reverse_engineer_credits(rows)
        ci = analysis["credit_inference"]
        return (
            f"🔍 *积分分析*\n\n"
            f"可持续基线: `{analysis['sustainable_rx']}` / `{analysis['sustainable_tx']}` Mbps\n"
            f"可突上限: `{analysis['burst_ceiling_rx']}` / `{analysis['burst_ceiling_tx']}` Mbps\n"
            f"钳位地板: `{analysis['throttle_floor']}` Mbps\n\n"
            f"突发: {ci['total_burst_count']} 次\n"
            f"钳位: {ci['total_throttle_count']} 次\n"
            f"模式: {ci['burst_to_throttle_pattern']}"
        ), _tg_main_keyboard()

    elif callback_data == "ip":
        from notifications import _get_ip_info
        info = _get_ip_info()
        ip_type = "云服务器" if info["hosting"] else "住宅IP"
        return (
            f"🌐 *IP 信息*\n\n"
            f"IP: `{info['ip']}`\n"
            f"位置: {info['country']} {info['city']}\n"
            f"ISP: {info['isp']}\n"
            f"类型: {ip_type}"
        ), _tg_main_keyboard()

    elif callback_data == "alerts":
        rt_st = "✓ 开启" if ROUTE_ALERT_ENABLED else "✗ 关闭"
        em_st = "✓ 启用" if EMAIL_ENABLED else "✗ 禁用"
        return (
            f"⚡ *告警状态*\n\n"
            f"钉钉: {'✓ 配置' if DINGTALK_WEBHOOK else '✗ 未配置'}\n"
            f"Telegram: {'✓ 配置' if TG_BOT_TOKEN else '✗ 未配置'}\n"
            f"邮件: {em_st}\n"
            f"路由告警: {rt_st}\n\n"
            f"磁盘阈值: {DISK_ALERT_MB} MB\n"
            f"日志保留: {LOG_RETENTION_DAYS} 天"
        ), _tg_main_keyboard()

    elif callback_data == "config":
        wh_st = "✓" if DINGTALK_WEBHOOK else "✗"
        tg_st = "✓" if TG_BOT_TOKEN else "✗"
        em_st = "✓" if EMAIL_ENABLED else "✗"
        xf_st = "✓" if XFYUN_ENABLED else "✗"
        return (
            f"⚙️ *配置状态*\n\n"
            f"钉钉: {wh_st}  Telegram: {tg_st}\n"
            f"邮件: {em_st}  讯飞AI: {xf_st}\n\n"
            f"服务器: `{SERVER_ALIAS}`\n"
            f"网卡: `{INTERFACE}`\n"
            f"路由: `{ROUTE_TARGET}` ({ROUTE_INTERVAL}s)"
        ), _tg_main_keyboard()

    elif callback_data == "refresh":
        return f"🔄 *已刷新*\n\n{_server_info_block()}", _tg_main_keyboard()

    # ── 子菜单：服务管理 ──
    elif callback_data == "svc_menu":
        return "🛠 *服务管理*\n\n选择操作:", _tg_svc_keyboard()

    elif callback_data == "svc_status":
        results = health_check()
        lines = ["📊 *服务状态*\n"]
        for r in results:
            if r["component"] in ("带宽采集", "路由监测", "每日分析"):
                icon = {"ok": "●", "warn": "⚠", "error": "✗"}.get(r["status"], "?")
                lines.append(f"`{icon}` {r['component']}: {r['message']}")
        return "\n".join(lines), _tg_svc_keyboard()

    elif callback_data == "svc_start_monitor":
        subprocess.run(["systemctl", "start", "bandwidth-monitor"], capture_output=True, timeout=10)
        return "✓ 带宽采集已启动", _tg_svc_keyboard()

    elif callback_data == "svc_stop_monitor":
        subprocess.run(["systemctl", "stop", "bandwidth-monitor"], capture_output=True, timeout=10)
        return "✓ 带宽采集已停止", _tg_svc_keyboard()

    elif callback_data == "svc_start_route":
        subprocess.run(["systemctl", "start", "route-monitor"], capture_output=True, timeout=10)
        return "✓ 路由监测已启动", _tg_svc_keyboard()

    elif callback_data == "svc_stop_route":
        subprocess.run(["systemctl", "stop", "route-monitor"], capture_output=True, timeout=10)
        return "✓ 路由监测已停止", _tg_svc_keyboard()

    elif callback_data == "svc_restart_all":
        for svc in ["bandwidth-monitor", "route-monitor"]:
            subprocess.run(["systemctl", "restart", svc], capture_output=True, timeout=10)
        return "✓ 全部服务已重启", _tg_svc_keyboard()

    # ── 子菜单：日志 ──
    elif callback_data == "log_menu":
        return "📋 *查看日志*\n\n选择服务:", _tg_log_keyboard()

    elif callback_data == "log_monitor":
        return _get_last_logs("bandwidth-monitor"), _tg_log_keyboard()

    elif callback_data == "log_route":
        return _get_last_logs("route-monitor"), _tg_log_keyboard()

    elif callback_data == "log_analyzer":
        return _get_last_logs("bandwidth-analyzer"), _tg_log_keyboard()

    elif callback_data == "log_system":
        return _get_last_logs("bandwidth-maintenance"), _tg_log_keyboard()

    elif callback_data == "clean_logs":
        from utils import rotate_logs
        result = rotate_logs()
        return f"🧹 *日志清理完成*\n\n删除: {result['deleted']} 个\n保留: {result['kept']} 个\n释放: {result['freed_mb']} MB", _tg_log_keyboard()

    # ── 返回主菜单 ──
    elif callback_data == "back_main":
        return f"🖥️ *{SERVER_ALIAS}*\n\n{_server_info_block()}", _tg_main_keyboard()

    return "❓ 未知操作", _tg_main_keyboard()


def _get_last_logs(service: str, lines: int = 15) -> str:
    """获取服务最近日志。"""
    try:
        result = subprocess.run(
            ["journalctl", "-u", service, "-n", str(lines), "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        if output:
            return f"📋 *{service} 最近日志*\n\n```\n{output}\n```"
        return f"📋 {service}: 暂无日志"
    except Exception:
        return f"⚠ 无法获取 {service} 日志"


def _tg_poll_thread():
    """后台线程：轮询 Telegram 回调。"""
    from notifications import poll_telegram_updates, send_telegram_keyboard

    offset = 0
    while is_running():
        if not TG_BOT_TOKEN:
            time.sleep(60)
            continue

        updates = poll_telegram_updates(offset, timeout=30)
        for update in updates:
            offset = update["update_id"] + 1

            callback = update.get("callback_query")
            if callback:
                data = callback.get("data", "")
                query_id = callback.get("id", "")
                result_text, keyboard = _handle_tg_callback(data, query_id)
                send_telegram_keyboard(result_text, keyboard)

        time.sleep(1)


def start_tg_poller():
    """启动 Telegram 回调轮询线程。"""
    if not TG_BOT_TOKEN:
        return
    t = threading.Thread(target=_tg_poll_thread, daemon=True)
    t.start()


def action_ip_check():
    """[18] IP探测"""
    clear_screen()
    step_frame("IP 属性探测")
    step_row(f"  正在查询...")
    step_end()

    import urllib.request
    import json as json_mod

    results = {}

    # API 1: ip-api.com (免费，无需 key)
    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/?fields=status,message,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,org,as,asname,reverse,mobile,proxy,hosting,query",
            headers={"User-Agent": "AWS-Monitor"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json_mod.loads(resp.read().decode("utf-8"))
            if data.get("status") == "success":
                results["ip"] = data.get("query", "N/A")
                results["country"] = data.get("country", "N/A")
                results["countryCode"] = data.get("countryCode", "N/A")
                results["region"] = data.get("regionName", "N/A")
                results["city"] = data.get("city", "N/A")
                results["isp"] = data.get("isp", "N/A")
                results["org"] = data.get("org", "N/A")
                results["as"] = data.get("as", "N/A")
                results["asname"] = data.get("asname", "N/A")
                results["mobile"] = data.get("mobile", False)
                results["proxy"] = data.get("proxy", False)
                results["hosting"] = data.get("hosting", False)
                results["lat"] = data.get("lat", 0)
                results["lon"] = data.get("lon", 0)
                results["timezone"] = data.get("timezone", "N/A")
    except Exception:
        pass

    # API 2: ip.sb (备用)
    if not results.get("ip"):
        try:
            req = urllib.request.Request(
                "https://api.ip.sb/geoip",
                headers={"User-Agent": "AWS-Monitor"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json_mod.loads(resp.read().decode("utf-8"))
                results["ip"] = data.get("ip", "N/A")
                results["country"] = data.get("country", "N/A")
                results["countryCode"] = data.get("country_code", "N/A")
                results["region"] = data.get("region", "N/A")
                results["city"] = data.get("city", "N/A")
                results["isp"] = data.get("isp", "N/A")
                results["org"] = data.get("organization", "N/A")
                results["as"] = data.get("asn", "N/A")
                results["asname"] = data.get("asn_organization", "N/A")
                results["hosting"] = data.get("is_cloud", False)
        except Exception:
            pass

    # API 3: 纯 IP 获取（兜底）
    if not results.get("ip"):
        try:
            results["ip"] = get_server_ip()
        except Exception:
            results["ip"] = "未知"

    # 显示结果
    clear_screen()
    step_frame("IP 属性探测结果")

    ip = results.get("ip", "N/A")
    step_row(f"  {c(BOLD, '基本信息')}")
    step_sep()
    step_row(f"  公网 IP:     {c(GREEN, ip)}")
    step_row(f"  国家:        {c(GREEN, results.get('country', 'N/A'))} ({results.get('countryCode', '')})")
    step_row(f"  地区:        {results.get('region', 'N/A')}")
    step_row(f"  城市:        {results.get('city', 'N/A')}")
    step_row(f"  时区:        {results.get('timezone', 'N/A')}")
    step_row(f"  坐标:        {results.get('lat', 'N/A')}, {results.get('lon', 'N/A')}")

    step_sep()
    step_row(f"  {c(BOLD, '网络信息')}")
    step_sep()
    step_row(f"  ISP:         {c(GREEN, results.get('isp', 'N/A'))}")
    step_row(f"  组织:        {results.get('org', 'N/A')}")
    step_row(f"  AS:          {results.get('as', 'N/A')}")
    step_row(f"  AS名称:      {results.get('asname', 'N/A')}")

    step_sep()
    step_row(f"  {c(BOLD, 'IP 属性')}")
    step_sep()

    # IP 类型判断
    is_hosting = results.get("hosting", False)
    is_proxy = results.get("proxy", False)
    is_mobile = results.get("mobile", False)

    if is_hosting:
        step_row(f"  IP 类型:     {c(YELLOW, '数据中心/云服务器')}")
    else:
        step_row(f"  IP 类型:     {c(GREEN, '住宅/家庭宽带')}")

    if is_proxy:
        step_row(f"  代理/VPN:    {c(YELLOW, '是')}")
    else:
        step_row(f"  代理/VPN:    {c(GREEN, '否')}")

    if is_mobile:
        step_row(f"  移动网络:    {c(YELLOW, '是')}")
    else:
        step_row(f"  移动网络:    {c(GREEN, '否')}")

    # 中国可用性评估
    step_sep()
    step_row(f"  {c(BOLD, '中国可用性评估')}")
    step_sep()

    asname = results.get("asname", "").upper()
    org = results.get("org", "").upper()
    is_aws = "AMAZON" in asname or "AMAZON" in org or "AWS" in asname

    if is_hosting and is_aws:
        step_row(f"  {c(YELLOW, '⚠ 检测到 AWS 云服务器 IP')}")
        step_row(f"  {c(DIM, '  云服务器 IP 在部分国内服务中可能受限')}")
        step_row(f"  {c(DIM, '  建议: 使用代理或中转方案优化连接')}")
    elif is_hosting:
        step_row(f"  {c(YELLOW, '⚠ 检测到数据中心 IP')}")
        step_row(f"  {c(DIM, '  可能被部分国内服务识别为机房 IP')}")
    else:
        step_row(f"  {c(GREEN, '✓ 住宅 IP，国内可用性较好')}")

    step_end()
    pause()


# ---------------------------------------------------------------------------
# 主菜单
# ---------------------------------------------------------------------------

def _is_first_run() -> bool:
    from config import CONFIG_FILE
    if not os.path.exists(CONFIG_FILE):
        return True
    try:
        import json
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
        if not data.get("DINGTALK_WEBHOOK") and not data.get("EMAIL_ENABLED"):
            return True
    except Exception:
        return True
    return False


def _first_run_wizard():
    clear_screen()
    step_frame("首次运行引导")
    step_row()
    step_row(f"  检测到配置文件为空，进入快速配置。")
    step_row(f"  可以跳过，稍后通过 [9] 编辑配置 设置。")
    step_row()
    step_end()
    options = ["进入配置向导", "跳过"]
    idx = ask_choice("选择", options)
    if idx == 0:
        action_edit_config()
    save_config()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="AWS Lightsail 服务器监控系统 v3.0")
    parser.add_argument("--check", action="store_true", help="健康自检")
    parser.add_argument("--analyze", action="store_true", help="运行积分分析")
    parser.add_argument("--status", action="store_true", help="查看监控状态")
    parser.add_argument("--report", action="store_true", help="发送钉钉日报")
    parser.add_argument("--rotate", action="store_true", help="清理过期日志")
    parser.add_argument("--version", action="store_true", help="检查版本更新")
    parser.add_argument("--update", action="store_true", help="一键更新")
    args = parser.parse_args()

    # CLI 快捷命令（非交互模式）
    if args.check or args.status:
        results = health_check()
        for r in results:
            icon = {"ok": "✓", "warn": "⚠", "error": "✗"}.get(r["status"], "?")
            print(f"  {icon}  {r['component']:<12} {r['message']}")
        return
    if args.analyze:
        from analyzer import find_latest_file, load_csv_files, reverse_engineer_credits, generate_report
        latest = find_latest_file()
        if not latest:
            print("  未找到数据文件")
            return
        rows = load_csv_files([latest])
        if not rows:
            print("  没有有效数据")
            return
        analysis = reverse_engineer_credits(rows)
        report = generate_report(rows, analysis, [latest])
        print(report)
        return
    if args.report:
        from reporter import get_traffic_data, build_dingtalk_message
        traffic = get_traffic_data()
        if traffic:
            msg = build_dingtalk_message(traffic)
            from notifications import send_dingtalk
            send_dingtalk("服务器流量日报", msg)
            print(f"  {c(GREEN, '✓')} 日报已发送")
        else:
            print(f"  {c(RED, '✗')} 无法获取流量数据")
        return
    if args.rotate:
        result = rotate_logs()
        print(f"  删除 {result['deleted']} 个文件，保留 {result['kept']} 个，释放 {result['freed_mb']} MB")
        return
    if args.version:
        print(f"  当前版本: {c(GREEN, CURRENT_VERSION)}")
        ver = check_version()
        if ver["update_available"]:
            print(f"  {c(YELLOW, '发现新版本')} {c(GREEN, ver['latest'])}")
        elif ver["latest"] != "unknown":
            print(f"  {c(GREEN, '已是最新版本')}")
        else:
            print(f"  {c(YELLOW, '无法连接 GitHub')}")
        return
    if args.update:
        print(f"  当前版本: {c(GREEN, CURRENT_VERSION)}")
        ver = check_version()
        if not ver["update_available"]:
            print(f"  {c(GREEN, '已是最新版本')}")
            return
        print(f"  {c(YELLOW, '发现新版本')} {c(GREEN, ver['latest'])}")
        result = do_update()
        if result["success"]:
            print(f"  {c(GREEN, '✓')} {result['message']}")
        else:
            print(f"  {c(RED, '✗')} {result['message']}")
        return

    # 首次运行引导
    if _is_first_run():
        _first_run_wizard()

    # 启动 Telegram 内联键盘轮询
    start_tg_poller()

    # 交互式菜单
    while True:
        clear_screen()

        # 磁盘告警
        alert = check_disk_alert()
        if alert:
            print(f"\n  {_cyan('+')}{_hline('=')}{_cyan('+')}")
            print(_full_row(f"  {c(RED, '!! 磁盘告警')}: 数据目录 {alert['total_mb']} MB / {alert['files']} 个文件"))
            print(f"  {_cyan('+')}{_hline('=')}{_cyan('+')}")

        # 主菜单
        print(f"\n  {_cyan('+')}{_hline('=')}{_cyan('+')}")
        print(_full_row(f"  {BOLD}AWS Lightsail 监控系统 v{CURRENT_VERSION}{NC}"))
        print(_full_row(f"  服务器: {c(GREEN, SERVER_ALIAS)}  |  IP: {c(GREEN, get_server_ip())}"))
        print(f"  {_cyan('+')}{_hline_split('-', 36, '-')}{_cyan('+')}")

        print(_full_row(f"  {BOLD}服务控制{NC}"))
        print(f"  {_cyan('+')}{_hline_split('-', 36, '-')}{_cyan('+')}")

        print(_2col_row(f"  {c(YELLOW, '[1]')}  查看状态", f"  {c(YELLOW, '[5]')}  实时日志"))
        print(_2col_row(f"  {c(YELLOW, '[2]')}  启动服务", f"  {c(YELLOW, '[6]')}  最近日志"))
        print(_2col_row(f"  {c(YELLOW, '[3]')}  停止服务", f"  {c(YELLOW, '[7]')}  手动推送"))
        print(_2col_row(f"  {c(YELLOW, '[4]')}  重启服务", f"  {c(YELLOW, '[8]')}  查看配置"))

        print(f"  {_cyan('+')}{_hline_split('-', 36, '-')}{_cyan('+')}")
        print(_full_row(f"  {BOLD}系统管理{NC}"))
        print(f"  {_cyan('+')}{_hline('=')}{_cyan('+')}")

        ai_st = c(GREEN, "已开启") if XFYUN_ENABLED else c(DIM, "已关闭")
        print(_3col_row(f"  {c(YELLOW, '[9]')}  编辑配置", f"  {c(YELLOW, '[10]')} 开机自启", f"  {c(YELLOW, '[11]')} 网卡与下载"))
        print(_3col_row(f"  {c(YELLOW, '[12]')} 卸载", f"  {c(YELLOW, '[13]')} 一键更新", f"  {c(YELLOW, '[14]')} 自动面板"))
        print(_3col_row(f"  {c(YELLOW, '[15]')} 流量与带宽", f"  {c(YELLOW, '[16]')} 告警设置", f"  {c(YELLOW, '[17]')} AI分析:{ai_st}"))
        print(_full_row(f"  {c(YELLOW, '[18]')}  IP探测"))

        print(f"  {_cyan('+')}{_hline('=')}{_cyan('+')}")
        print(_full_row(f"  {c(DIM, '[0]')}  {c(DIM, '退出')}"))
        print(f"  {_cyan('+')}{_hline('=')}{_cyan('+')}")

        try:
            choice = input(f"\n  {c(YELLOW, '请选择')} [0-18 | q退出 r刷新]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        # 快捷键
        if choice == "q":
            break
        if choice == "r":
            continue

        actions = {
            "0": None,
            "1": action_status,
            "2": action_start,
            "3": action_stop,
            "4": action_restart,
            "5": action_realtime_log,
            "6": action_recent_log,
            "7": action_manual_push,
            "8": action_view_config,
            "9": action_edit_config,
            "10": action_boot_toggle,
            "11": action_nic_info,
            "12": action_uninstall,
            "13": action_update,
            "14": action_auto_panel,
            "15": action_traffic,
            "16": action_alert_settings,
            "17": action_ai_analysis,
            "18": action_ip_check,
        }

        if choice == "0":
            break
        elif choice in actions:
            actions[choice]()
        else:
            print(f"  {c(RED, '无效输入')}")
            pause()


if __name__ == "__main__":
    main()
