#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用工具函数模块

从各脚本中提取的纯函数，无外部依赖。
"""

from __future__ import annotations

import os
import signal
import datetime
import time
import hashlib
import shutil
import subprocess
import tempfile

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


_IP_CACHE = {"value": None, "ts": 0.0}
_IP_CACHE_TTL = 600.0   # 秒；IP 不会一分钟变一次，避免每条日报都去打外网 API


def get_server_ip() -> str:
    """获取服务器 IP 地址（优先公网 IP，进程内缓存 10 分钟）。"""
    now = time.time()
    if _IP_CACHE["value"] and (now - _IP_CACHE["ts"]) < _IP_CACHE_TTL:
        return _IP_CACHE["value"]

    try:
        import requests
        response = requests.get("https://api.ipify.org", timeout=5)
        if response.status_code == 200:
            ip = response.text.strip()
            if ip:
                _IP_CACHE["value"] = ip
                _IP_CACHE["ts"] = now
                return ip
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

    # 5. 磁盘剩余空间
    try:
        st = os.statvfs(data_dir)
        free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
        if free_mb > 500:
            results.append({"component": "磁盘剩余", "status": "ok",
                            "message": f"剩余 {free_mb:.0f} MB"})
        elif free_mb > 100:
            results.append({"component": "磁盘剩余", "status": "warn",
                            "message": f"剩余 {free_mb:.0f} MB（空间不足）"})
        else:
            results.append({"component": "磁盘剩余", "status": "error",
                            "message": f"剩余 {free_mb:.0f} MB（严重不足）"})
    except Exception:
        results.append({"component": "磁盘剩余", "status": "warn", "message": "无法检测"})

    # 6. 数据目录体积（与每日维护告警使用同一个阈值 DISK_ALERT_MB，
    #    免得面板说"一切正常"而维护任务天天告警）
    try:
        from config import DISK_ALERT_MB
        total_mb = sum(os.path.getsize(f) for f in files) / (1024 * 1024) if files else 0.0
        if total_mb < DISK_ALERT_MB:
            results.append({"component": "数据目录", "status": "ok",
                            "message": f"{total_mb:.0f} MB / 阈值 {DISK_ALERT_MB} MB"})
        else:
            results.append({"component": "数据目录", "status": "warn",
                            "message": f"{total_mb:.0f} MB 已超阈值 {DISK_ALERT_MB} MB"})
    except Exception:
        results.append({"component": "数据目录", "status": "warn", "message": "无法检测"})

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
        url = f"https://raw.githubusercontent.com/linjunhao024-byte/AWS-server-monitoring/main/monitor/config.py?t={int(time.time())}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "AWS-Monitor",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8")
            match = re.search(r'CURRENT_VERSION\s*=\s*"([^"]+)"', content)
            if match:
                latest = match.group(1)
                result["latest"] = latest
                if latest != CURRENT_VERSION:
                    result["update_available"] = True
    except Exception as e:
        result["error"] = str(e)

    return result


DEFAULT_UPDATE_URL = (
    "https://github.com/linjunhao024-byte/AWS-server-monitoring/"
    "archive/refs/heads/main.tar.gz"
)
UPDATE_SERVICES = [
    "bandwidth-monitor.service",
    "route-monitor.service",
    "bandwidth-analyzer.timer",
    "bandwidth-maintenance.timer",
    "bandwidth-daily-report.timer",
]
MAX_UPDATE_BYTES = 50 * 1024 * 1024
MIN_UPDATE_BYTES = 1024


def _run_systemctl(args, timeout: int = 10) -> tuple[bool, str]:
    """执行 systemctl 并返回 (是否成功, stderr/失败原因)。"""
    try:
        proc = subprocess.run(
            ["systemctl"] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return False, err or f"systemctl 返回码 {proc.returncode}"
        return True, err
    except Exception as exc:
        return False, str(exc)


def _replace_path(src: str, dst: str) -> None:
    """用 src 替换 dst（文件或目录）。"""
    if os.path.isdir(dst) and not os.path.islink(dst):
        shutil.rmtree(dst)
    elif os.path.exists(dst) or os.path.islink(dst):
        os.remove(dst)
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def _download_update(url: str, dest: str,
                     expected_sha256: str | None = None) -> tuple[bool, str]:
    """流式下载更新包，校验非空/大小/可选 sha256。"""
    try:
        import requests
    except ImportError as exc:
        return False, f"缺少 requests 依赖: {exc}"

    digest = hashlib.sha256()
    total = 0
    try:
        with requests.get(url, stream=True, timeout=(5, 30)) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_UPDATE_BYTES:
                        return False, f"更新包过大（超过 {MAX_UPDATE_BYTES // 1024 // 1024} MB）"
                    fh.write(chunk)
                    digest.update(chunk)
    except Exception as exc:
        return False, f"下载失败: {exc}"

    if total == 0:
        return False, "下载失败: 更新包为空"
    if total < MIN_UPDATE_BYTES:
        return False, f"下载失败: 更新包过小（{total} 字节）"
    if expected_sha256:
        actual = digest.hexdigest().lower()
        if actual != expected_sha256.strip().lower():
            return False, f"sha256 校验失败: 期望 {expected_sha256[:12]}… 实际 {actual[:12]}…"
    return True, ""


def _rollback_update(install_dir: str, backup_dir: str, backed: list[str],
                     created: list[str], unit_dir: str,
                     unit_backups: dict[str, bytes],
                     unit_created: list[str],
                     services: list[str]) -> list[str]:
    """把已替换/新建的文件和 systemd 单元恢复原状；返回回滚过程中的错误。"""
    errors = []
    for name in created:
        path = os.path.join(install_dir, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            elif os.path.exists(path) or os.path.islink(path):
                os.remove(path)
        except OSError as exc:
            errors.append(f"删除 {name}: {exc}")

    for name in backed:
        src = os.path.join(backup_dir, name)
        dst = os.path.join(install_dir, name)
        try:
            _replace_path(src, dst)
        except OSError as exc:
            errors.append(f"恢复 {name}: {exc}")

    for name in unit_created:
        path = os.path.join(unit_dir, name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            errors.append(f"删除单元 {name}: {exc}")
    for name, data in unit_backups.items():
        try:
            with open(os.path.join(unit_dir, name), "wb") as fh:
                fh.write(data)
        except OSError as exc:
            errors.append(f"恢复单元 {name}: {exc}")

    if unit_backups or unit_created:
        _run_systemctl(["daemon-reload"], timeout=15)
    for svc in services:
        _run_systemctl(["restart", svc], timeout=10)
    return errors


def do_update(install_dir: str | None = None,
              config_file: str | None = None,
              unit_dir: str = "/etc/systemd/system",
              services: list[str] | None = None,
              tar_url: str = DEFAULT_UPDATE_URL,
              expected_sha256: str | None = None) -> dict:
    """
    一键更新：从 GitHub 拉取最新代码，保留配置，重启服务。

    下载/解压/替换/重启任一步失败都会回滚旧文件；只有全部服务重启成功
    才返回 success=True。参数默认从 config 读取，显式传入便于离线测试。
    """
    from config import INSTALL_DIR, CONFIG_FILE

    install_dir = install_dir or INSTALL_DIR
    config_file = config_file or CONFIG_FILE
    services = list(services or UPDATE_SERVICES)
    expected_sha256 = expected_sha256 or os.environ.get("AWS_MONITOR_UPDATE_SHA256")

    result = {"success": False, "message": "", "version": ""}
    tmp_dir = tempfile.mkdtemp(prefix="aws_monitor_update_")
    tar_path = os.path.join(tmp_dir, "update.tar.gz")
    backup_dir = os.path.join(tmp_dir, "backup")
    backed: list[str] = []
    created: list[str] = []
    unit_backups: dict[str, bytes] = {}
    unit_created: list[str] = []
    units_synced = 0

    try:
        # 1. 下载（超时 + 流式写盘 + 大小/sha256 校验）
        ok, err = _download_update(tar_url, tar_path, expected_sha256)
        if not ok:
            result["message"] = err
            return result

        # 2. 解压：拒绝绝对路径、.. 与指向外部的链接（tar 路径穿越）
        import tarfile

        def _safe_members(members, dest):
            dest_abs = os.path.abspath(dest)
            for m in members:
                target = os.path.abspath(os.path.join(dest, m.name))
                if not target.startswith(dest_abs + os.sep):
                    raise ValueError(f"包内含非法路径: {m.name}")
                if m.issym() or m.islnk():
                    raise ValueError(f"包内含链接成员: {m.name}")
                yield m

        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                tar.extractall(tmp_dir, members=_safe_members(tar.getmembers(), tmp_dir))
        except Exception as exc:
            result["message"] = f"解压失败: {exc}"
            return result

        extracted_dir = os.path.join(tmp_dir, "AWS-server-monitoring-main", "monitor")
        if not os.path.isdir(extracted_dir):
            result["message"] = "下载解压失败：未找到 monitor 目录"
            return result

        # 3. 备份旧文件后替换（settings.json 与 __pycache__ 不动）
        skip_files = {"settings.json", "__pycache__"}
        os.makedirs(backup_dir, exist_ok=True)
        try:
            for name in os.listdir(extracted_dir):
                if name in skip_files:
                    continue
                src = os.path.join(extracted_dir, name)
                dst = os.path.join(install_dir, name)
                if os.path.exists(dst) or os.path.islink(dst):
                    bdst = os.path.join(backup_dir, name)
                    if os.path.isdir(dst) and not os.path.islink(dst):
                        shutil.copytree(dst, bdst)
                    else:
                        shutil.copy2(dst, bdst, follow_symlinks=False)
                    backed.append(name)
                else:
                    created.append(name)
                _replace_path(src, dst)
        except Exception as exc:
            rollback_errors = _rollback_update(
                install_dir, backup_dir, backed, created,
                unit_dir, unit_backups, unit_created, services,
            )
            result["message"] = f"文件更新失败: {exc}"
            if rollback_errors:
                result["message"] += "；回滚异常: " + "; ".join(rollback_errors)
            return result

        # 4. 同步 systemd 单元（先备份，失败可回滚）
        src_unit_dir = os.path.join(install_dir, "systemd")
        if os.path.isdir(src_unit_dir) and os.path.isdir(unit_dir):
            try:
                for name in os.listdir(src_unit_dir):
                    if not (name.endswith(".service") or name.endswith(".timer")):
                        continue
                    if name.startswith("bandwidth-data-check"):
                        continue  # 一次性安装校验单元，不该被复制回来
                    src = os.path.join(src_unit_dir, name)
                    dst = os.path.join(unit_dir, name)
                    if os.path.exists(dst):
                        with open(dst, "rb") as fh:
                            unit_backups[name] = fh.read()
                    else:
                        unit_created.append(name)
                    shutil.copy2(src, dst)
                    units_synced += 1
            except Exception as exc:
                rollback_errors = _rollback_update(
                    install_dir, backup_dir, backed, created,
                    unit_dir, unit_backups, unit_created, services,
                )
                result["message"] = f"systemd 单元同步失败: {exc}"
                if rollback_errors:
                    result["message"] += "；回滚异常: " + "; ".join(rollback_errors)
                return result

            if units_synced:
                ok, err = _run_systemctl(["daemon-reload"], timeout=15)
                if not ok:
                    rollback_errors = _rollback_update(
                        install_dir, backup_dir, backed, created,
                        unit_dir, unit_backups, unit_created, services,
                    )
                    result["message"] = f"daemon-reload 失败: {err}"
                    if rollback_errors:
                        result["message"] += "；回滚异常: " + "; ".join(rollback_errors)
                    return result

        # 5. 重启所有服务，任一失败即回滚
        restarted = []
        for svc in services:
            ok, err = _run_systemctl(["restart", svc], timeout=10)
            if not ok:
                rollback_errors = _rollback_update(
                    install_dir, backup_dir, backed, created,
                    unit_dir, unit_backups, unit_created, services,
                )
                result["message"] = f"服务 {svc} 重启失败: {err}"
                if rollback_errors:
                    result["message"] += "；回滚异常: " + "; ".join(rollback_errors)
                return result
            restarted.append(svc)

        # 6. 读取新版本号
        new_version = "unknown"
        try:
            with open(os.path.join(install_dir, "config.py"), "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("CURRENT_VERSION"):
                        new_version = line.split('"')[1]
                        break
        except Exception:
            pass

        result["success"] = True
        result["version"] = new_version
        result["message"] = (
            f"更新成功 → v{new_version}，已重启 {len(restarted)} 个服务"
            + (f"，同步 {units_synced} 个 systemd 单元" if units_synced else "")
        )
        return result
    finally:
        # 成功或已回滚才清理；回滚失败时保留临时目录，便于人工恢复。
        if result["success"]:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            rollback_failed = "回滚异常" in result.get("message", "")
            if not rollback_failed:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            else:
                result["message"] += f"（备份保留在 {tmp_dir}）"
