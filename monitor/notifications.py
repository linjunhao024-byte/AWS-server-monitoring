#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一通知层模块

合并 3 份钉钉实现 + 邮件 + LLM 调用。
钉钉使用 urllib（零依赖），邮件使用 smtplib（标准库）。
"""

from __future__ import annotations

import os
import sys
import json
import hmac
import time
import socket
import base64
import hashlib
import smtplib
import urllib.request
import urllib.parse
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

from config import (
    DINGTALK_WEBHOOK, DINGTALK_SECRET,
    TG_BOT_TOKEN, TG_CHAT_ID, TG_API_URL,
    XFYUN_API_URL, XFYUN_API_KEY, XFYUN_MODEL, XFYUN_ENABLED,
    EMAIL_ENABLED, SMTP_SERVER, SMTP_PORT, SMTP_USE_SSL,
    SMTP_USERNAME, SMTP_PASSWORD, SENDER_NAME, EMAIL_RECIPIENTS,
)


# ---------------------------------------------------------------------------
# 钉钉推送
# ---------------------------------------------------------------------------

def send_dingtalk(title: str, markdown_text: str,
                  webhook: str = None, secret: str = None) -> bool:
    """
    通过钉钉 Webhook 发送 Markdown 消息（零依赖，使用 urllib）。

    Args:
        title: 消息标题
        markdown_text: Markdown 格式的消息内容
        webhook: Webhook URL（默认使用配置文件中的值）
        secret: 加签密钥（默认使用配置文件中的值）

    Returns:
        是否发送成功
    """
    webhook = webhook or DINGTALK_WEBHOOK
    secret = secret or DINGTALK_SECRET

    if not webhook:
        print("[WARN] 钉钉 Webhook 未配置，跳过推送。", file=sys.stderr)
        return False

    try:
        url = webhook
        # 加签是可选的：机器人只开"自定义关键词"时也能发，
        # 这种情况下不能带 timestamp/sign，否则钉钉会以签名错误拒收。
        if secret:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(
                secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            url = f"{webhook}&timestamp={timestamp}&sign={sign}"

        payload = json.dumps({
            "msgtype": "markdown",
            "markdown": {"title": title, "text": markdown_text},
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("errcode") == 0:
                print(f"[OK] 钉钉消息发送成功")
                return True
            else:
                print(f"[ERR] 钉钉返回: {result}", file=sys.stderr)
                return False
    except Exception as exc:
        print(f"[ERR] 钉钉发送失败: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Telegram 推送
# ---------------------------------------------------------------------------

def send_telegram(text: str, parse_mode: str = "Markdown") -> bool:
    """
    发送 Telegram 消息。

    Args:
        text: 消息内容
        parse_mode: Markdown 或 HTML

    Returns:
        是否发送成功
    """
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False

    try:
        url = f"{TG_API_URL}/bot{TG_BOT_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except Exception as exc:
        print(f"[WARN] Telegram 发送失败: {exc}", file=sys.stderr)
        return False


def send_telegram_keyboard(text: str, buttons: list[dict],
                           parse_mode: str = "Markdown") -> bool:
    """
    发送带内联键盘的 Telegram 消息。

    Args:
        text: 消息内容
        buttons: 按钮列表，每项 {"text": "...", "callback_data": "..."}
                 支持多行：嵌套列表 [[btn1, btn2], [btn3]]
        parse_mode: Markdown 或 HTML

    Returns:
        是否发送成功
    """
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False

    # 构建键盘
    if buttons and isinstance(buttons[0], list):
        keyboard = buttons  # 已经是多行格式
    else:
        keyboard = [buttons]  # 单行，包成二维

    try:
        url = f"{TG_API_URL}/bot{TG_BOT_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": keyboard,
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except Exception as exc:
        print(f"[WARN] Telegram 键盘发送失败: {exc}", file=sys.stderr)
        return False


def answer_callback(callback_query_id: str, text: str = "") -> bool:
    """回答 Telegram 回调查询（消除加载状态）。"""
    if not TG_BOT_TOKEN:
        return False
    try:
        url = f"{TG_API_URL}/bot{TG_BOT_TOKEN}/answerCallbackQuery"
        payload = json.dumps({
            "callback_query_id": callback_query_id,
            "text": text,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok", False)
    except Exception:
        return False


TG_POLL_WARN_AFTER = 5
_tg_poll_state = {
    "consecutive_failures": 0,
    "http4xx_logged": False,
    "last_error": "",
    "last_error_kind": "",
}


def reset_telegram_poll_state() -> None:
    """重置轮询失败计数（测试或重新启动轮询时使用）。"""
    _tg_poll_state.update({
        "consecutive_failures": 0,
        "http4xx_logged": False,
        "last_error": "",
        "last_error_kind": "",
    })


def get_telegram_poll_state() -> dict:
    """返回轮询失败状态副本，供面板/测试查看。"""
    return dict(_tg_poll_state)


def _tg_poll_failed(kind: str, detail: str, debug: bool = False) -> None:
    """记录一次轮询失败；连续失败达到阈值时只打一条 WARNING。"""
    state = _tg_poll_state
    state["consecutive_failures"] += 1
    state["last_error"] = detail[:200]
    state["last_error_kind"] = kind
    count = state["consecutive_failures"]
    if debug:
        print(f"[DEBUG] Telegram 轮询失败（{kind}，连续 {count} 次）: {detail[:200]}",
              file=sys.stderr)
    if count == TG_POLL_WARN_AFTER:
        print(f"[WARN] Telegram 轮询已连续失败 {count} 次，面板回调可能不可用："
              f"{kind} {detail[:200]}", file=sys.stderr)


def poll_telegram_updates(offset: int = 0, timeout: int = 30) -> list[dict]:
    """
    长轮询 Telegram 更新（用于接收内联键盘回调）。

    异常不再被静默吞掉：超时打 DEBUG、HTTP 4xx 打一次 ERROR、解析错误带
    原始响应前 200 字；连续失败达到阈值打一条 WARNING。

    Returns:
        update 列表；失败时返回空列表，但失败原因记录在 _tg_poll_state。
    """
    if not TG_BOT_TOKEN:
        return []

    url = f"{TG_API_URL}/bot{TG_BOT_TOKEN}/getUpdates?offset={offset}&timeout={timeout}"
    req = urllib.request.Request(url, headers={"User-Agent": "AWS-Monitor"})

    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(200).decode("utf-8", "replace")
        except Exception:
            body = ""
        if 400 <= exc.code < 500:
            if not _tg_poll_state["http4xx_logged"]:
                print(f"[ERROR] Telegram 轮询 HTTP {exc.code}: {body[:200]}",
                      file=sys.stderr)
                _tg_poll_state["http4xx_logged"] = True
            _tg_poll_failed(f"http_{exc.code}", body or str(exc))
        else:
            print(f"[ERROR] Telegram 轮询 HTTP {exc.code}: {body[:200]}",
                  file=sys.stderr)
            _tg_poll_failed(f"http_{exc.code}", body or str(exc))
        return []
    except (socket.timeout, TimeoutError) as exc:
        _tg_poll_failed("timeout", str(exc) or "请求超时", debug=True)
        return []
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            _tg_poll_failed("timeout", str(reason), debug=True)
        else:
            print(f"[ERROR] Telegram 轮询网络错误: {reason}", file=sys.stderr)
            _tg_poll_failed("network", str(reason))
        return []
    except Exception as exc:
        print(f"[ERROR] Telegram 轮询异常 {type(exc).__name__}: {str(exc)[:200]}",
              file=sys.stderr)
        _tg_poll_failed("exception", f"{type(exc).__name__}: {exc}")
        return []

    try:
        result = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        print(f"[ERROR] Telegram 轮询响应解析失败: {str(exc)[:200]}；"
              f"原文: {raw[:200]!r}", file=sys.stderr)
        _tg_poll_failed("parse", f"{exc}; raw={raw[:200]!r}")
        return []

    if not isinstance(result, dict) or not result.get("ok"):
        detail = str(result)[:200]
        print(f"[ERROR] Telegram 轮询返回 ok=false: {detail}", file=sys.stderr)
        _tg_poll_failed("api", detail)
        return []

    _tg_poll_state.update({
        "consecutive_failures": 0,
        "http4xx_logged": False,
        "last_error": "",
        "last_error_kind": "",
    })
    return result.get("result", [])


def _tg_main_keyboard() -> list:
    """主菜单键盘。"""
    return [
        [
            {"text": "📊 状态", "callback_data": "status"},
            {"text": "📈 流量", "callback_data": "traffic"},
            {"text": "🔍 分析", "callback_data": "analyze"},
        ],
        [
            {"text": "📡 路由", "callback_data": "route"},
            {"text": "🌐 IP", "callback_data": "ip"},
            {"text": "⚡ 告警", "callback_data": "alerts"},
        ],
        [
            {"text": "🛠 服务", "callback_data": "svc_menu"},
            {"text": "📋 日志", "callback_data": "log_menu"},
        ],
        [
            {"text": "⚙️ 配置", "callback_data": "config"},
            {"text": "🔄 刷新", "callback_data": "refresh"},
        ],
    ]


def _tg_svc_keyboard() -> list:
    """服务管理子菜单键盘。"""
    return [
        [
            {"text": "▶️ 启动采集", "callback_data": "svc_start_monitor"},
            {"text": "⏹ 停止采集", "callback_data": "svc_stop_monitor"},
        ],
        [
            {"text": "▶️ 启动路由", "callback_data": "svc_start_route"},
            {"text": "⏹ 停止路由", "callback_data": "svc_stop_route"},
        ],
        [
            {"text": "🔄 重启全部", "callback_data": "svc_restart_all"},
            {"text": "📊 服务状态", "callback_data": "svc_status"},
        ],
        [
            {"text": "⬅️ 返回主菜单", "callback_data": "back_main"},
        ],
    ]


def _tg_log_keyboard() -> list:
    """日志子菜单键盘。"""
    return [
        [
            {"text": "采集日志", "callback_data": "log_monitor"},
            {"text": "路由日志", "callback_data": "log_route"},
        ],
        [
            {"text": "分析日志", "callback_data": "log_analyzer"},
            {"text": "系统日志", "callback_data": "log_system"},
        ],
        [
            {"text": "🧹 清理旧日志", "callback_data": "clean_logs"},
        ],
        [
            {"text": "⬅️ 返回主菜单", "callback_data": "back_main"},
        ],
    ]


def send_tg_with_menu(text: str) -> bool:
    """发送带主菜单键盘的 Telegram 消息。"""
    return send_telegram_keyboard(text, _tg_main_keyboard())


# ---------------------------------------------------------------------------
# 邮件发送
# ---------------------------------------------------------------------------

def send_email(subject: str, html_content: str,
               chart_path: str = None) -> bool:
    """
    发送 HTML 邮件，可选附带趋势图。

    Args:
        subject: 邮件主题
        html_content: HTML 格式的邮件正文
        chart_path: 趋势图图片路径（可选）

    Returns:
        是否发送成功
    """
    if not EMAIL_ENABLED:
        print("[跳过] 邮件功能未启用")
        return True

    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print("[WARN] SMTP 未配置，跳过邮件发送。", file=sys.stderr)
        return False

    if not EMAIL_RECIPIENTS:
        print("[WARN] 未配置收件人，跳过邮件发送。", file=sys.stderr)
        return False

    try:
        msg = MIMEMultipart('related')
        msg['Subject'] = subject
        msg['From'] = f"{SENDER_NAME} <{SMTP_USERNAME}>"
        msg['To'] = ', '.join(EMAIL_RECIPIENTS)

        html_part = MIMEText(html_content, 'html', 'utf-8')
        msg.attach(html_part)

        if chart_path and os.path.exists(chart_path):
            with open(chart_path, 'rb') as f:
                img = MIMEImage(f.read())
                img.add_header('Content-ID', '<trend_chart>')
                img.add_header('Content-Disposition', 'inline',
                               filename='trend_chart.png')
                msg.attach(img)

        if SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()

        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_USERNAME, EMAIL_RECIPIENTS, msg.as_string())
        server.quit()

        print(f"[OK] 邮件发送成功！收件人: {', '.join(EMAIL_RECIPIENTS)}")
        return True

    except smtplib.SMTPAuthenticationError:
        print("[ERR] 邮箱认证失败，请检查用户名和密码", file=sys.stderr)
        return False
    except smtplib.SMTPException as e:
        print(f"[ERR] SMTP 错误: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[ERR] 发送邮件异常: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# 讯飞星火大模型调用
# ---------------------------------------------------------------------------

def call_xfyun(prompt: str) -> str | None:
    """
    调用讯飞星火 API 进行分析（流式响应）。

    Args:
        prompt: 发给模型的 prompt

    Returns:
        模型回复文本，失败返回 None
    """
    if not XFYUN_ENABLED or not XFYUN_API_KEY:
        print("[WARN] 讯飞 API 未配置，跳过 LLM 分析。", file=sys.stderr)
        return None

    try:
        import requests as req_lib

        headers = {
            "Authorization": f"Bearer {XFYUN_API_KEY}",
            "Content-Type": "application/json",
        }
        body = {
            "model": XFYUN_MODEL,
            "user": "bandwidth_analyzer",
            "messages": [
                {
                    "role": "system",
                    "content": "你是一位云计算和 AIOps 专家，擅长分析服务器带宽数据和云厂商积分机制。请用简洁的中文回答，控制在300字以内。"
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 600,
            "stream": True,
        }

        response = req_lib.post(
            url=XFYUN_API_URL,
            json=body,
            headers=headers,
            stream=True,
            timeout=120,
        )
        response.raise_for_status()

        full_response = ""
        for chunk in response.iter_lines():
            if not chunk or "[DONE]" in str(chunk):
                continue
            # 跳过 "data: " 前缀
            data_str = chunk[6:] if chunk.startswith(b"data: ") else chunk
            try:
                data = json.loads(data_str)
                delta = data["choices"][0]["delta"]
                if "content" in delta and delta["content"]:
                    full_response += delta["content"]
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

        return full_response if full_response else None

    except Exception as exc:
        print(f"[WARN] 讯飞 API 调用失败: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# IP 信息获取（用于通知模板）
# ---------------------------------------------------------------------------

_cached_ip_info = None

def _get_ip_info() -> dict:
    """获取 IP 信息（带缓存，一次会话只查一次）。"""
    global _cached_ip_info
    if _cached_ip_info is not None:
        return _cached_ip_info

    info = {"ip": "未知", "country": "", "city": "", "isp": "", "hosting": False}

    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/?fields=query,country,city,isp,hosting",
            headers={"User-Agent": "AWS-Monitor"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "success":
                info["ip"] = data.get("query", "未知")
                info["country"] = data.get("country", "")
                info["city"] = data.get("city", "")
                info["isp"] = data.get("isp", "")
                info["hosting"] = data.get("hosting", False)
    except Exception:
        pass

    _cached_ip_info = info
    return info


def _server_info_block() -> str:
    """生成服务器信息块（用于通知模板）。"""
    from config import SERVER_ALIAS, INTERFACE
    ip_info = _get_ip_info()
    ip = ip_info["ip"]
    loc = f"{ip_info['country']} {ip_info['city']}".strip()
    isp = ip_info["isp"]
    ip_type = "云服务器" if ip_info["hosting"] else "住宅IP"
    return (
        f"- **服务器**: {SERVER_ALIAS}\n"
        f"- **IP**: {ip} ({loc})\n"
        f"- **ISP**: {isp}\n"
        f"- **类型**: {ip_type}\n"
        f"- **网卡**: {INTERFACE}"
    )


# ---------------------------------------------------------------------------
# 预定义通知模板
# ---------------------------------------------------------------------------

def _sign() -> str:
    """通知签名。"""
    from logo import SIGNATURE
    return f"\n\n{SIGNATURE}"


def notify_service_start(service_name: str, detail: str = ""):
    """服务启动通知（精简版）。"""
    from utils import now_iso
    from config import SERVER_ALIAS
    msg = f"✅ *{service_name}* 已启动 `{SERVER_ALIAS}` {now_iso()}{_sign()}"
    if detail:
        msg += f"\n{detail}"
    send_dingtalk(f"✅ {service_name}", msg)


def notify_service_stop(service_name: str, reason: str = "正常停止"):
    """服务停止通知（精简版）。"""
    from utils import now_iso
    from config import SERVER_ALIAS
    msg = f"⚠️ *{service_name}* 已停止 `{SERVER_ALIAS}` {now_iso()}\n原因: {reason}{_sign()}"
    send_dingtalk(f"⚠️ {service_name} 已停止", msg)


def notify_data_summary():
    """发送数据采集验证消息（安装后 10 分钟调用）。"""
    from utils import now_iso, format_bytes
    from config import INTERFACE, DATA_DIR, SERVER_ALIAS
    import os
    import glob

    ts = now_iso()

    # 检查最新 CSV 文件
    pattern = os.path.join(DATA_DIR, f"traffic_log_*_{INTERFACE}.csv")
    files = sorted(glob.glob(pattern))

    if not files:
        msg = (
            f"### ⚠️ 数据验证\n\n"
            f"{_server_info_block()}\n"
            f"- **时间**: {ts}\n"
            f"- **状态**: 未找到数据文件\n\n"
            f"> 请检查 bandwidth-monitor 服务是否正常运行\n\n"
            f"---\n*安装后自动验证*"
        )
        send_dingtalk("⚠️ 数据验证：未找到数据", msg)
        return

    latest = files[-1]
    mtime = os.path.getmtime(latest)
    size = os.path.getsize(latest)

    # 统计行数
    line_count = 0
    try:
        with open(latest, "r", encoding="utf-8") as f:
            for line in f:
                if not line.startswith("#"):
                    line_count += 1
    except Exception:
        line_count = -1

    # 读取最后一条数据
    last_line = ""
    try:
        with open(latest, "r", encoding="utf-8") as f:
            for line in f:
                if not line.startswith("#") and line.strip():
                    last_line = line.strip()
    except Exception:
        pass

    msg = (
        f"### 📊 数据采集验证\n\n"
        f"{_server_info_block()}\n"
        f"- **时间**: {ts}\n"
        f"- **文件**: `{os.path.basename(latest)}`\n"
        f"- **大小**: {format_bytes(size)}\n"
        f"- **采样数**: {line_count:,} 条\n"
    )

    if last_line and "," in last_line:
        parts = last_line.split(",")
        if len(parts) >= 4:
            msg += f"- **最新数据**: {parts[0]} | RX={parts[1]} Mbps | TX={parts[2]} Mbps\n"

    msg += (
        f"\n> ✅ 数据采集正常运行中\n\n"
        f"---\n*安装后自动验证*"
    )
    send_dingtalk("📊 数据采集验证通过", msg)

    # 清理一次性 timer
    try:
        import subprocess
        subprocess.run(["systemctl", "stop", "bandwidth-data-check.timer"],
                       capture_output=True, timeout=5)
        subprocess.run(["systemctl", "disable", "bandwidth-data-check.timer"],
                       capture_output=True, timeout=5)
        # 删除 service 和 timer 文件
        for f in ["/etc/systemd/system/bandwidth-data-check.service",
                  "/etc/systemd/system/bandwidth-data-check.timer"]:
            if os.path.exists(f):
                os.remove(f)
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=5)
    except Exception:
        pass


def send_weekly_email_report() -> bool:
    """生成并发送邮件周报（HTML + 趋势图）。

    由 --daily-report 在"今天是配置里的周报发送日"时调用：
    以前这个功能只在菜单 [7] 里能手动触发，README 却宣称有自动周报，
    用户配好 SMTP 也永远收不到。
    """
    if not EMAIL_ENABLED:
        return False
    try:
        from reporter import get_traffic_data, build_email_html, generate_trend_chart_image
        from utils import get_iso_week_info
        from config import SERVER_ALIAS

        traffic = get_traffic_data()
        if not traffic:
            print("[WARN] 无法获取流量数据，跳过周报邮件", file=sys.stderr)
            return False

        chart_path = generate_trend_chart_image(traffic.daily_data)
        html = build_email_html(traffic, chart_path)
        week_info = get_iso_week_info()
        subject = f"服务器流量周报 - 第{week_info['week']}周 - {SERVER_ALIAS}"
        ok = send_email(subject, html, chart_path)
        if chart_path and os.path.exists(chart_path):
            try:
                os.remove(chart_path)
            except OSError:
                pass
        print(f"[{'OK' if ok else 'ERR'}] 周报邮件{'已发送' if ok else '发送失败'}")
        return ok
    except Exception as exc:
        print(f"[ERR] 周报邮件生成失败: {exc}", file=sys.stderr)
        return False


def send_daily_report() -> bool:
    """生成并发送每日钉钉报告；按真实发送结果打印，返回是否成功。"""
    from reporter import build_daily_detail_message
    from config import SERVER_ALIAS

    msg = build_daily_detail_message()
    ok = send_dingtalk(f"📊 {SERVER_ALIAS} 每日报告", msg)
    from utils import now_iso
    if ok:
        print(f"[{now_iso()}] 每日报告已推送")
    else:
        print(f"[{now_iso()}] [ERR] 每日报告发送失败：钉钉未配置或推送失败",
              file=sys.stderr)
    return ok


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) > 1:
        if _sys.argv[1] == "--test":
            # 安装后测试消息
            from utils import now_iso
            msg = (
                f"### 🔔 钉钉连接测试\n\n"
                f"{_server_info_block()}\n"
                f"- **时间**: {now_iso()}\n\n"
                f"> ✅ 钉钉 Webhook 配置正确，连接成功\n\n"
                f"---\n*安装向导自动发送*"
            )
            ok = send_dingtalk("🔔 钉钉连接测试", msg)
            _sys.exit(0 if ok else 1)

        elif _sys.argv[1] == "--data-check":
            # 10 分钟后数据验证
            notify_data_summary()
            _sys.exit(0)

        elif _sys.argv[1] == "--start" and len(_sys.argv) > 2:
            # 服务启动通知
            notify_service_start(_sys.argv[2])
            _sys.exit(0)

        elif _sys.argv[1] == "--stop" and len(_sys.argv) > 2:
            # 服务停止通知
            notify_service_stop(_sys.argv[2])
            _sys.exit(0)

        elif _sys.argv[1] == "--maintenance":
            # 每日维护：日志轮转 + 磁盘告警
            from utils import rotate_logs, check_disk_alert, now_iso
            from config import LOG_RETENTION_DAYS, SERVER_ALIAS

            # 日志轮转
            result = rotate_logs()
            if result["deleted"] > 0:
                print(f"[{now_iso()}] 日志轮转: 删除 {result['deleted']} 个文件，释放 {result['freed_mb']} MB")

            # 磁盘告警
            alert = check_disk_alert()
            if alert:
                msg = (
                    f"### ⚠️ 磁盘空间告警\n\n"
                    f"{_server_info_block()}\n"
                    f"- **时间**: {now_iso()}\n"
                    f"- **数据目录**: {alert['total_mb']} MB / {alert['files']} 个文件\n"
                    f"- **告警阈值**: {alert['threshold_mb']} MB\n\n"
                    f"> 建议: 菜单 [9] 编辑配置 → [8] 运维参数 调整阈值，或清理过期日志\n\n"
                    f"---\n*每日维护自动检测*"
                )
                send_dingtalk("⚠️ 磁盘空间告警", msg)
                print(f"[{now_iso()}] 磁盘告警: {alert['total_mb']} MB 超过阈值 {alert['threshold_mb']} MB")

            _sys.exit(0)

        elif _sys.argv[1] == "--daily-report":
            # 每日报告（钉钉）；若今天是配置的周报发送日，顺带发邮件周报
            from config import WEEKLY_REPORT_DAY, EMAIL_ENABLED
            import datetime as _datetime

            ok = send_daily_report()

            try:
                weekday = int(WEEKLY_REPORT_DAY)
            except (TypeError, ValueError):
                weekday = 6
            if EMAIL_ENABLED and _datetime.datetime.now().weekday() == weekday:
                send_weekly_email_report()

            # 失败时返回非 0，让 systemd 把 daily-report 标成 failed，
            # 不再出现“日志说已推送、实际没发出去”的假成功。
            _sys.exit(0 if ok else 1)
