#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用工具函数模块

从各脚本中提取的纯函数，无外部依赖。
"""

import os
import signal
import datetime
import time

# ---------------------------------------------------------------------------
# 全局优雅退出标志
# ---------------------------------------------------------------------------
_running = True


def _signal_handler(signum, frame):
    """捕获 SIGTERM / SIGINT，设置退出标志。"""
    global _running
    _running = False


def setup_signal_handler():
    """注册信号处理器，用于 daemon 优雅退出。"""
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)


def is_running() -> bool:
    """检查是否应该继续运行。"""
    return _running


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """返回当前时间 ISO 格式字符串。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_label() -> str:
    """返回当天日期标签 YYYYMMDD。"""
    return datetime.datetime.now().strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 格式化工具
# ---------------------------------------------------------------------------

# 流量单位阈值
GB_THRESHOLD = 1024 * 1024 * 1024
MB_THRESHOLD = 1024 * 1024
KB_THRESHOLD = 1024

BYTES_PER_MBIT = 125000  # 1 Mbps = 125,000 Bytes/s


def format_bytes(bytes_value: int) -> str:
    """将字节数转换为人类可读的格式。"""
    if bytes_value >= GB_THRESHOLD:
        return f"{bytes_value / GB_THRESHOLD:.2f} GB"
    elif bytes_value >= MB_THRESHOLD:
        return f"{bytes_value / MB_THRESHOLD:.2f} MB"
    elif bytes_value >= KB_THRESHOLD:
        return f"{bytes_value / KB_THRESHOLD:.2f} KB"
    else:
        return f"{bytes_value} B"


def format_duration(seconds: int) -> str:
    """格式化时长。"""
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        return f"{seconds // 60}分{seconds % 60}秒"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}小时{m}分"


def bytes_to_mbps(byte_delta: int, interval: float) -> float:
    """将字节差值转换为 Mbps（精确到小数点后 4 位）。"""
    return round((byte_delta / BYTES_PER_MBIT) / interval, 4)


def calculate_growth_rate(current: float, previous: float) -> float | None:
    """计算增长率。"""
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100


def format_growth_rate(rate: float | None) -> str:
    """格式化增长率显示。"""
    if rate is None:
        return "N/A"
    if rate >= 0:
        return f"⬆️ +{rate:.1f}%"
    else:
        return f"⬇️ {rate:.1f}%"


def get_server_ip() -> str:
    """获取服务器 IP 地址（优先公网）。"""
    try:
        import requests
        response = requests.get("https://api.ipify.org", timeout=5)
        if response.status_code == 200:
            return response.text.strip()
    except Exception:
        pass

    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "未知IP"


def get_iso_week_info() -> dict:
    """获取 ISO 周数信息。"""
    today = datetime.datetime.now()
    iso_year, iso_week, iso_weekday = today.isocalendar()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)

    return {
        "year": iso_year,
        "week": iso_week,
        "weekday": iso_weekday,
        "week_start": monday,
        "week_end": sunday,
        "week_start_str": monday.strftime("%m-%d"),
        "week_end_str": sunday.strftime("%m-%d"),
        "days_elapsed": iso_weekday,
    }


# ---------------------------------------------------------------------------
# 健康自检
# ---------------------------------------------------------------------------

def health_check(interface: str = None, data_dir: str = None) -> list[dict]:
    """
    一键诊断所有组件状态。

    Returns:
        [{"component": str, "status": "ok"|"warn"|"error", "message": str}, ...]
    """
    import subprocess
    import glob as glob_mod
    from config import INTERFACE, DATA_DIR, DINGTALK_WEBHOOK

    interface = interface or INTERFACE
    data_dir = data_dir or DATA_DIR
    results = []

    # 1. 网卡
    if os.path.exists(f"/sys/class/net/{interface}/statistics/rx_bytes"):
        results.append({"component": "网卡", "status": "ok", "message": f"{interface} 存在"})
    else:
        results.append({"component": "网卡", "status": "error", "message": f"{interface} 不存在"})

    # 2. systemd 服务
    for svc, desc in [("bandwidth-monitor", "带宽采集"), ("route-monitor", "路由监测")]:
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            st = r.stdout.strip()
            if st == "active":
                results.append({"component": desc, "status": "ok", "message": "运行中"})
            else:
                results.append({"component": desc, "status": "error", "message": st or "未运行"})
        except Exception:
            results.append({"component": desc, "status": "warn", "message": "无法检测"})

    # 3. 数据文件
    pattern = os.path.join(data_dir, f"traffic_log_*_{interface}.csv")
    files = sorted(glob_mod.glob(pattern))
    if files:
        latest = files[-1]
        mtime = os.path.getmtime(latest)
        age_sec = time.time() - mtime
        if age_sec < 120:
            results.append({"component": "数据采集", "status": "ok",
                            "message": f"活跃 ({int(age_sec)}秒前更新)"})
        elif age_sec < 3600:
            results.append({"component": "数据采集", "status": "warn",
                            "message": f"最后更新 {int(age_sec/60)} 分钟前"})
        else:
            results.append({"component": "数据采集", "status": "error",
                            "message": f"最后更新 {int(age_sec/3600)} 小时前"})
    else:
        results.append({"component": "数据采集", "status": "error", "message": "未找到数据文件"})

    # 4. 钉钉连通性
    if DINGTALK_WEBHOOK:
        results.append({"component": "钉钉", "status": "ok", "message": "已配置"})
    else:
        results.append({"component": "钉钉", "status": "warn", "message": "未配置"})

    # 5. 磁盘空间
    try:
        st = os.statvfs(data_dir)
        free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
        if free_mb > 500:
            results.append({"component": "磁盘空间", "status": "ok",
                            "message": f"剩余 {free_mb:.0f} MB"})
        elif free_mb > 100:
            results.append({"component": "磁盘空间", "status": "warn",
                            "message": f"剩余 {free_mb:.0f} MB（空间不足）"})
        else:
            results.append({"component": "磁盘空间", "status": "error",
                            "message": f"剩余 {free_mb:.0f} MB（严重不足）"})
    except Exception:
        results.append({"component": "磁盘空间", "status": "warn", "message": "无法检测"})

    return results


# ---------------------------------------------------------------------------
# 日志轮转
# ---------------------------------------------------------------------------

def rotate_logs(data_dir: str = None, retention_days: int = None) -> dict:
    """
    清理过期的 CSV 和路由日志。

    Returns:
        {"deleted": int, "kept": int, "freed_mb": float}
    """
    import glob as glob_mod
    from config import DATA_DIR, LOG_RETENTION_DAYS

    data_dir = data_dir or DATA_DIR
    retention_days = retention_days or LOG_RETENTION_DAYS

    cutoff = time.time() - retention_days * 86400
    deleted = 0
    kept = 0
    freed_bytes = 0

    patterns = [
        os.path.join(data_dir, "traffic_log_*.csv"),
        os.path.join(data_dir, "route_log_*.txt"),
    ]

    for pattern in patterns:
        for fpath in glob_mod.glob(pattern):
            if os.path.getmtime(fpath) < cutoff:
                size = os.path.getsize(fpath)
                try:
                    os.remove(fpath)
                    deleted += 1
                    freed_bytes += size
                except OSError:
                    kept += 1
            else:
                kept += 1

    return {
        "deleted": deleted,
        "kept": kept,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
    }


# ---------------------------------------------------------------------------
# 磁盘告警
# ---------------------------------------------------------------------------

def check_disk_alert(data_dir: str = None, threshold_mb: int = None) -> dict | None:
    """
    检查数据目录大小，超阈值返回告警信息。

    Returns:
        None if OK, or {"total_mb": float, "threshold_mb": int, "files": int}
    """
    import glob as glob_mod
    from config import DATA_DIR, DISK_ALERT_MB

    data_dir = data_dir or DATA_DIR
    threshold_mb = threshold_mb or DISK_ALERT_MB

    total_bytes = 0
    file_count = 0

    for fpath in glob_mod.glob(os.path.join(data_dir, "*")):
        if os.path.isfile(fpath):
            total_bytes += os.path.getsize(fpath)
            file_count += 1

    total_mb = total_bytes / (1024 * 1024)

    if total_mb > threshold_mb:
        return {
            "total_mb": round(total_mb, 1),
            "threshold_mb": threshold_mb,
            "files": file_count,
        }
    return None


# ---------------------------------------------------------------------------
# 版本检查
# ---------------------------------------------------------------------------

def check_version() -> dict:
    """
    对比 GitHub 最新版本（通过读取 config.py 中的 CURRENT_VERSION）。

    Returns:
        {"current": str, "latest": str, "update_available": bool}
    """
    import urllib.request
    import re
    from config import CURRENT_VERSION

    result = {"current": CURRENT_VERSION, "latest": "unknown", "update_available": False}

    try:
        url = "https://raw.githubusercontent.com/linjunhao024-byte/AWS-server-monitoring/main/monitor/config.py"
        req = urllib.request.Request(url, headers={"User-Agent": "AWS-Monitor"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8")
            match = re.search(r'CURRENT_VERSION\s*=\s*"([^"]+)"', content)
            if match:
                latest = match.group(1)
                result["latest"] = latest
                if latest != CURRENT_VERSION:
                    result["update_available"] = True
    except Exception:
        pass

    return result


def do_update() -> dict:
    """
    一键更新：从 GitHub 拉取最新代码，保留配置，重启服务。

    Returns:
        {"success": bool, "message": str, "version": str}
    """
    import subprocess
    import shutil
    import tempfile
    from config import INSTALL_DIR, CONFIG_FILE

    result = {"success": False, "message": "", "version": ""}

    # 1. 下载最新代码到临时目录
    try:
        tmp_dir = tempfile.mkdtemp(prefix="aws_monitor_update_")
        tar_url = "https://github.com/linjunhao024-byte/AWS-server-monitoring/archive/refs/heads/main.tar.gz"
        tar_path = os.path.join(tmp_dir, "update.tar.gz")

        import urllib.request
        urllib.request.urlretrieve(tar_url, tar_path)

        # 解压
        import tarfile
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(tmp_dir)

        # 找到解压后的 monitor 目录
        extracted_dir = os.path.join(tmp_dir, "AWS-server-monitoring-main", "monitor")
        if not os.path.isdir(extracted_dir):
            result["message"] = "下载解压失败：未找到 monitor 目录"
            return result

    except Exception as e:
        result["message"] = f"下载失败: {e}"
        return result

    # 2. 备份当前配置
    backup_config = None
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                backup_config = f.read()
        except Exception:
            pass

    # 3. 复制新文件（跳过 settings.json）
    try:
        skip_files = {"settings.json", "__pycache__"}
        for name in os.listdir(extracted_dir):
            if name in skip_files:
                continue
            src = os.path.join(extracted_dir, name)
            dst = os.path.join(INSTALL_DIR, name)
            if os.path.isdir(src):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
    except Exception as e:
        result["message"] = f"文件更新失败: {e}"
        return result

    # 4. 恢复配置
    if backup_config:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                f.write(backup_config)
            os.chmod(CONFIG_FILE, 0o600)
        except Exception:
            pass

    # 5. 重启服务
    services = ["bandwidth-monitor.service", "route-monitor.service"]
    restarted = []
    for svc in services:
        try:
            subprocess.run(["systemctl", "restart", svc],
                           capture_output=True, timeout=10)
            restarted.append(svc)
        except Exception:
            pass

    # 6. 清理临时文件
    try:
        shutil.rmtree(tmp_dir)
    except Exception:
        pass

    # 7. 读取新版本号
    new_version = "unknown"
    try:
        new_config = os.path.join(INSTALL_DIR, "config.py")
        with open(new_config, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("CURRENT_VERSION"):
                    new_version = line.split('"')[1]
                    break
    except Exception:
        pass

    result["success"] = True
    result["version"] = new_version
    result["message"] = f"更新成功 → v{new_version}，已重启 {len(restarted)} 个服务"
    return result
