#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一通知层模块

合并 3 份钉钉实现 + 邮件 + LLM 调用。
钉钉使用 urllib（零依赖），邮件使用 smtplib（标准库）。
"""

import os
import sys
import json
import hmac
import time
import base64
import hashlib
import smtplib
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

from config import (
    DINGTALK_WEBHOOK, DINGTALK_SECRET,
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

    if not webhook or not secret:
        print("[WARN] 钉钉 Webhook 未配置，跳过推送。", file=sys.stderr)
        return False

    try:
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
                    "content": "你是一位云计算和 AIOps 专家，擅长分析服务器带宽数据和云厂商积分机制。请用结构化的中文回答。"
                },
                {"role": "user", "content": prompt},
            ],
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
