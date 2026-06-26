#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AWS Lightsail 服务器监控系统 v3.0

终端菜单入口，整合所有功能。
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
    LOG_RETENTION_DAYS, DISK_ALERT_MB, CURRENT_VERSION,
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

def frame_line(char: str = "=", width: int = W) -> str:
    return c(CYAN, char * width)


def menu_header(title: str):
    """主菜单标题行。"""
    print()
    print(f"  {c(CYAN, '+')}{frame_line('=')}{c(CYAN, '+')}")
    print(f"  {c(CYAN, '|')}{c(BOLD, f'  {title}')}{_pad('', W - 4 - _display_width(title))}  {c(CYAN, '|')}")
    print(f"  {c(CYAN, '+')}{frame_line('-', 35)}{c(CYAN, '+')}{frame_line('-', 36)}{c(CYAN, '+')}")


def menu_section(title: str):
    """分区标题。"""
    print(f"  {c(CYAN, '|')}{c(BOLD, f'  {title}')}{_pad('', W - 4 - _display_width(title))}  {c(CYAN, '|')}")
    print(f"  {c(CYAN, '+')}{frame_line('=')}{c(CYAN, '+'  )}")


def menu_row_2col(left: str, right: str):
    """双列菜单行。"""
    left_padded = _pad(left, 35)
    print(f"  {c(CYAN, '|')}{left_padded}{c(CYAN, '|')}{right}{_pad('', 36 - _display_width(_strip_ansi(right)))}{c(CYAN, '|')}")


def menu_row_3col(c1: str, c2: str, c3: str):
    """三列菜单行。"""
    col_w = 23
    p1 = _pad(c1, col_w)
    p2 = _pad(c2, col_w)
    p3 = _pad(c3, col_w)
    print(f"  {c(CYAN, '|')}{p1}{c(CYAN, '|')}{p2}{c(CYAN, '|')}{p3}{c(CYAN, '|')}")


def menu_full_row(text: str):
    """全宽行。"""
    print(f"  {c(CYAN, '|')}{text}{_pad('', W - _display_width(_strip_ansi(text)))}{c(CYAN, '|')}")


def menu_footer():
    print(f"  {c(CYAN, '+')}{frame_line('=')}{c(CYAN, '+')}")


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
            choice = int(input(f"  {c(YELLOW, prompt)} [0-{len(options)}]: "))
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
    print(f"\n  {c(DIM, 'Ctrl+C 退出实时日志')}\n")
    try:
        subprocess.run(["journalctl", "-u", svc, "-f", "--no-pager"], timeout=3600)
    except KeyboardInterrupt:
        pass
    except subprocess.TimeoutExpired:
        pass


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
    pause()


def action_manual_push():
    """[7] 手动推送"""
    clear_screen()
    step_frame("手动推送")
    options = ["发送钉钉测试消息", "发送钉钉日报", "发送邮件周报"]
    idx = ask_choice("选择操作", options)
    if idx == -1:
        return

    from notifications import send_dingtalk, send_email

    if idx == 0:
        if not DINGTALK_WEBHOOK:
            print(f"\n  {c(YELLOW, '钉钉未配置')}")
            pause()
            return
        msg = f"### 钉钉连接测试\n\n- **服务器**: {SERVER_ALIAS}\n- **IP**: {get_server_ip()}\n- **时间**: {now_iso()}\n\n> 测试成功\n\n---\n*菜单手动测试*"
        ok = send_dingtalk("钉钉连接测试", msg)
        print(f"\n  {c(GREEN, '✓') if ok else c(RED, '✗')} {'发送成功' if ok else '发送失败'}")

    elif idx == 1:
        from reporter import get_traffic_data, build_dingtalk_message
        traffic = get_traffic_data()
        if traffic:
            msg = build_dingtalk_message(traffic)
            send_dingtalk("服务器流量日报", msg)
            print(f"\n  {c(GREEN, '✓')} 日报已发送")
        else:
            print(f"\n  {c(YELLOW, '无法获取流量数据')}")

    elif idx == 2:
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
    pause()


def action_view_config():
    """[8] 查看配置"""
    clear_screen()
    step_frame("当前配置")
    step_row(f"  服务器别名   {c(GREEN, SERVER_ALIAS)}")
    step_row(f"  网卡         {c(GREEN, INTERFACE)}")
    step_row(f"  数据目录     {c(DIM, DATA_DIR)}")
    step_sep()
    webhook_disp = c(GREEN, _mask(DINGTALK_WEBHOOK)) if DINGTALK_WEBHOOK else c(DIM, "(未配置)")
    step_row(f"  钉钉         {webhook_disp}")
    xfyun_st = c(GREEN, "已启用") if XFYUN_ENABLED else c(DIM, "已禁用")
    step_row(f"  讯飞星火     {xfyun_st}")
    email_st = c(GREEN, "已启用") if EMAIL_ENABLED else c(DIM, "已禁用")
    step_row(f"  邮件         {email_st}")
    step_sep()
    step_row(f"  路由目标     {c(GREEN, ROUTE_TARGET)}")
    step_row(f"  路由间隔     {ROUTE_INTERVAL} 秒")
    step_row(f"  日志保留     {LOG_RETENTION_DAYS} 天")
    step_row(f"  磁盘告警     {DISK_ALERT_MB} MB")
    step_end()
    pause()


def action_edit_config():
    """[9] 编辑配置"""
    clear_screen()
    step_frame("编辑配置")
    import config as cfg

    options = [
        "修改服务器别名",
        "配置钉钉 Webhook",
        "配置讯飞星火 LLM",
        "配置邮件",
        "修改路由监测参数",
        "修改日志保留天数",
        "修改磁盘告警阈值",
    ]
    idx = ask_choice("选择操作", options)
    if idx == -1:
        return

    if idx == 0:
        alias = input(f"\n  服务器别名 [{c(GREEN, SERVER_ALIAS)}]: ").strip()
        if alias:
            cfg.SERVER_ALIAS = alias
    elif idx == 1:
        webhook = input(f"\n  Webhook URL: ").strip()
        secret = input(f"  Secret (SEC开头): ").strip()
        if webhook:
            cfg.DINGTALK_WEBHOOK = webhook
        if secret:
            cfg.DINGTALK_SECRET = secret
    elif idx == 2:
        api_key = input(f"\n  讯飞 API Key: ").strip()
        if api_key:
            cfg.XFYUN_API_KEY = api_key
            cfg.XFYUN_ENABLED = True
        else:
            cfg.XFYUN_API_KEY = ""
            cfg.XFYUN_ENABLED = False
    elif idx == 3:
        if input(f"\n  启用邮件？(y/n): ").strip().lower() == 'y':
            cfg.EMAIL_ENABLED = True
            u = input(f"  SMTP 用户名 [{SMTP_USERNAME or ''}]: ").strip()
            if u: cfg.SMTP_USERNAME = u
            p = input(f"  SMTP 密码: ").strip()
            if p: cfg.SMTP_PASSWORD = p
            r = input(f"  收件人（逗号分隔）: ").strip()
            if r: cfg.EMAIL_RECIPIENTS = [x.strip() for x in r.split(",")]
        else:
            cfg.EMAIL_ENABLED = False
    elif idx == 4:
        t = input(f"\n  路由目标 [{ROUTE_TARGET}]: ").strip()
        if t: cfg.ROUTE_TARGET = t
        i = input(f"  检测间隔(秒) [{ROUTE_INTERVAL}]: ").strip()
        if i:
            try: cfg.ROUTE_INTERVAL = int(i)
            except ValueError: print(f"  {c(YELLOW, '无效数字')}")
    elif idx == 5:
        d = input(f"\n  日志保留天数 [{LOG_RETENTION_DAYS}]: ").strip()
        if d:
            try: cfg.LOG_RETENTION_DAYS = int(d)
            except ValueError: print(f"  {c(YELLOW, '无效数字')}")
    elif idx == 6:
        m = input(f"\n  磁盘告警阈值(MB) [{DISK_ALERT_MB}]: ").strip()
        if m:
            try: cfg.DISK_ALERT_MB = int(m)
            except ValueError: print(f"  {c(YELLOW, '无效数字')}")

    save_config()
    print(f"\n  {c(GREEN, '✓')} 配置已保存")
    pause()


def action_boot_toggle():
    """[10] 开机自启"""
    clear_screen()
    step_frame("开机自启管理")
    services = [
        ("bandwidth-monitor.service", "带宽采集"),
        ("route-monitor.service", "路由监测"),
        ("bandwidth-analyzer.timer", "每日分析"),
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
    if input(f"  {c(RED, '确认卸载？(y/N)'): ").strip().lower() != 'y':
        print(f"\n  已取消")
        pause()
        return

    services = ["bandwidth-monitor", "route-monitor", "bandwidth-analyzer.timer",
                "bandwidth-maintenance.timer", "bandwidth-data-check.timer"]
    for svc in services:
        subprocess.run(["systemctl", "stop", svc], capture_output=True, timeout=5)
        subprocess.run(["systemctl", "disable", svc], capture_output=True, timeout=5)
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
        print(f"\n  {c(YELLOW, '按回车重启菜单以加载新版本...')}")
        input()
        os.execv(sys.executable, [sys.executable] + sys.argv)
    pause()


def action_auto_panel():
    """[14] 自动面板开关"""
    clear_screen()
    step_frame("自动面板")
    step_row(f"  当前状态: {'运行中' if True else '已停止'}")
    step_end()
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
    step_row(f"  钉钉告警:  {'已配置' if DINGTALK_WEBHOOK else c(YELLOW, '未配置')}")
    step_row(f"  邮件告警:  {'已启用' if EMAIL_ENABLED else c(DIM, '未启用')}")
    step_row(f"  磁盘阈值:  {DISK_ALERT_MB} MB")
    step_row(f"  日志保留:  {LOG_RETENTION_DAYS} 天")
    step_sep()
    step_row(f"  {c(DIM, '修改请进入 [9] 编辑配置')}")
    step_end()
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

    # CLI 快捷命令
    if args.check:
        action_status()
        return
    if args.analyze:
        action_ai_analysis()
        return
    if args.status:
        action_status()
        return
    if args.report:
        action_manual_push()
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

    # 交互式菜单
    while True:
        clear_screen()

        # 磁盘告警
        alert = check_disk_alert()
        if alert:
            print(f"\n  {c(CYAN, '+')}{frame_line('=')}{c(CYAN, '+')}")
            menu_full_row(f"  {c(RED, '!! 磁盘告警')}: 数据目录 {alert['total_mb']} MB / {alert['files']} 个文件")
            menu_footer()

        # 主菜单
        print(f"\n  {c(CYAN, '+')}{frame_line('=')}{c(CYAN, '+')}")
        menu_full_row(f"  {BOLD}AWS Lightsail 监控系统 v{CURRENT_VERSION}{NC}")
        menu_full_row(f"  服务器: {c(GREEN, SERVER_ALIAS)}  |  IP: {c(GREEN, get_server_ip())}")
        print(f"  {c(CYAN, '+')}{frame_line('-', 35)}{c(CYAN, '+')}{frame_line('-', 36)}{c(CYAN, '+')}")

        menu_full_row(f"  {BOLD}服务控制{NC}")
        print(f"  {c(CYAN, '+')}{frame_line('-', 35)}{c(CYAN, '+')}{frame_line('-', 36)}{c(CYAN, '+')}")

        menu_row_2col(f"  {c(YELLOW, '[1]')}  查看状态", f"  {c(YELLOW, '[5]')}  实时日志")
        menu_row_2col(f"  {c(YELLOW, '[2]')}  启动服务", f"  {c(YELLOW, '[6]')}  最近日志")
        menu_row_2col(f"  {c(YELLOW, '[3]')}  停止服务", f"  {c(YELLOW, '[7]')}  手动推送")
        menu_row_2col(f"  {c(YELLOW, '[4]')}  重启服务", f"  {c(YELLOW, '[8]')}  查看配置")

        print(f"  {c(CYAN, '+')}{frame_line('-', 35)}{c(CYAN, '+')}{frame_line('-', 36)}{c(CYAN, '+')}")
        menu_full_row(f"  {BOLD}系统管理{NC}")
        print(f"  {c(CYAN, '+')}{frame_line('=')}{c(CYAN, '+')}{frame_line('=')}{c(CYAN, '+')}")

        ai_st = c(GREEN, "已开启") if XFYUN_ENABLED else c(DIM, "已关闭")
        menu_row_3col(f"  {c(YELLOW, '[9]')}  编辑配置", f"  {c(YELLOW, '[10]')} 开机自启", f"  {c(YELLOW, '[11]')} 网卡与下载")
        menu_row_3col(f"  {c(YELLOW, '[12]')} 卸载", f"  {c(YELLOW, '[13]')} 一键更新", f"  {c(YELLOW, '[14]')} 自动面板")
        menu_row_3col(f"  {c(YELLOW, '[15]')} 流量与带宽", f"  {c(YELLOW, '[16]')} 告警设置", f"  {c(YELLOW, '[17]')} AI分析:{ai_st}")

        print(f"  {c(CYAN, '+')}{frame_line('=')}{c(CYAN, '+')}{frame_line('=')}{c(CYAN, '+')}")
        menu_full_row(f"  {c(DIM, '[0]')}  {c(DIM, '退出')}")
        print(f"  {c(CYAN, '+')}{frame_line('=')}{c(CYAN, '+')}")

        try:
            choice = input(f"\n  {c(YELLOW, '请选择')} [0-17]: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

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
