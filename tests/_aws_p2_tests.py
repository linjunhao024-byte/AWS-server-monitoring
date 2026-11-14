#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AWS-server-monitoring 七条 P2 的离线回归测试。

不联网、不调用真实 systemctl、不写 /etc、不执行 install/uninstall。
所有外部依赖（requests/urlopen/subprocess/shutil.disk_usage）都在测试内 mock。
"""

from __future__ import annotations

import io
import os
import shutil
import socket
import sys
import tarfile
import tempfile
import types
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONITOR_DIR = os.path.join(ROOT, "monitor")
sys.path.insert(0, MONITOR_DIR)

import main  # noqa: E402
import monitor_daemon  # noqa: E402
import notifications  # noqa: E402
import reporter  # noqa: E402
import route_daemon  # noqa: E402
import utils  # noqa: E402


class _FakeResponse:
    def __init__(self, data: bytes = b"", status: int = 200):
        self.data = data
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n: int = -1):
        return self.data if n < 0 else self.data[:n]

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def iter_content(self, chunk_size: int = 65536):
        for i in range(0, len(self.data), chunk_size):
            yield self.data[i:i + chunk_size]


class _FakeRequests(types.ModuleType):
    def __init__(self, get_func):
        super().__init__("requests")
        self.get = get_func


def _install_fake_requests(get_func):
    old = sys.modules.get("requests")
    sys.modules["requests"] = _FakeRequests(get_func)
    return old


def _restore_requests(old):
    if old is None:
        sys.modules.pop("requests", None)
    else:
        sys.modules["requests"] = old


def _make_update_tar(path: str, config_text: str):
    data = config_text.encode("utf-8")
    info = tarfile.TarInfo("AWS-server-monitoring-main/monitor/config.py")
    info.size = len(data)
    with tarfile.open(path, "w:gz") as tar:
        tar.addfile(info, io.BytesIO(data))


class TestSamplingSchedule(unittest.TestCase):
    def test_next_sleep_seconds(self):
        self.assertEqual(monitor_daemon.next_sleep_seconds(100.0, 101.0), 1.0)
        self.assertEqual(monitor_daemon.next_sleep_seconds(101.5, 101.0), 0.0)
        self.assertEqual(monitor_daemon.next_sleep_seconds(100.25, 101.0), 0.75)

    def test_advance_deadline_skips_missed_ticks(self):
        # 处理耗时 2.4s：deadline 只推进到下一个未来节拍，不连续补采样。
        self.assertEqual(
            monitor_daemon.advance_deadline(103.4, 101.0, step=1.0), 104.0
        )
        self.assertEqual(
            monitor_daemon.advance_deadline(100.2, 100.0, step=1.0), 101.0
        )


class TestUpdateRollback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aws_p2_update_")
        self.install = os.path.join(self.tmp, "install")
        self.unit_dir = os.path.join(self.tmp, "units")
        os.makedirs(self.install)
        os.makedirs(self.unit_dir)
        self.old_config = 'CURRENT_VERSION = "1.0.0"\n'
        with open(os.path.join(self.install, "config.py"), "w", encoding="utf-8") as fh:
            fh.write(self.old_config)
        self.tar_path = os.path.join(self.tmp, "update.tar.gz")
        _make_update_tar(self.tar_path, 'CURRENT_VERSION = "9.9.9"\n')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_download_timeout(self):
        old = _install_fake_requests(lambda *a, **k: (_ for _ in ()).throw(
            TimeoutError("timed out")))
        try:
            dest = os.path.join(self.tmp, "x.tar.gz")
            ok, err = utils._download_update("http://example.invalid/u.tar.gz", dest)
        finally:
            _restore_requests(old)
        self.assertFalse(ok)
        self.assertIn("下载失败", err)

    def test_download_empty_file(self):
        old = _install_fake_requests(
            lambda *a, **k: _FakeResponse(b"")
        )
        try:
            dest = os.path.join(self.tmp, "empty.tar.gz")
            ok, err = utils._download_update("http://example.invalid/u.tar.gz", dest)
        finally:
            _restore_requests(old)
        self.assertFalse(ok)
        self.assertIn("为空", err)

    def test_restart_failure_rolls_back(self):
        def fake_download(url, dest, expected=None):
            shutil.copy2(self.tar_path, dest)
            return True, ""

        with mock.patch.object(utils, "_download_update", side_effect=fake_download), \
             mock.patch.object(utils, "_run_systemctl", return_value=(False, "restart boom")):
            result = utils.do_update(
                install_dir=self.install,
                config_file=os.path.join(self.install, "settings.json"),
                unit_dir=self.unit_dir,
                services=["bandwidth-monitor.service"],
                tar_url="http://example.invalid/u.tar.gz",
            )

        self.assertFalse(result["success"])
        self.assertIn("重启失败", result["message"])
        with open(os.path.join(self.install, "config.py"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.old_config)

    def test_success_only_after_restart_ok(self):
        def fake_download(url, dest, expected=None):
            shutil.copy2(self.tar_path, dest)
            return True, ""

        with mock.patch.object(utils, "_download_update", side_effect=fake_download), \
             mock.patch.object(utils, "_run_systemctl", return_value=(True, "")):
            result = utils.do_update(
                install_dir=self.install,
                config_file=os.path.join(self.install, "settings.json"),
                unit_dir=self.unit_dir,
                services=["bandwidth-monitor.service"],
                tar_url="http://example.invalid/u.tar.gz",
            )

        self.assertTrue(result["success"])
        with open(os.path.join(self.install, "config.py"), encoding="utf-8") as fh:
            self.assertIn("9.9.9", fh.read())


class TestNotificationResult(unittest.TestCase):
    def test_daily_report_failure_does_not_print_sent(self):
        with mock.patch.object(reporter, "build_daily_detail_message", return_value="x"), \
             mock.patch.object(notifications, "send_dingtalk", return_value=False):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                ok = notifications.send_daily_report()
        self.assertFalse(ok)
        self.assertNotIn("已推送", out.getvalue())
        self.assertIn("发送失败", err.getvalue())

    def test_route_alert_failure_does_not_print_sent(self):
        with mock.patch.object(route_daemon, "build_route_alert", return_value="x"), \
             mock.patch.object(route_daemon, "send_dingtalk", return_value=False):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                ok = route_daemon.send_route_alert("t", {}, [])
        self.assertFalse(ok)
        self.assertNotIn("已发送", out.getvalue())
        self.assertIn("发送失败", err.getvalue())


class TestTelegramPoll(unittest.TestCase):
    def setUp(self):
        notifications.reset_telegram_poll_state()
        self._old_token = notifications.TG_BOT_TOKEN
        notifications.TG_BOT_TOKEN = "test-token"

    def tearDown(self):
        notifications.TG_BOT_TOKEN = self._old_token
        notifications.reset_telegram_poll_state()

    def test_timeout_is_debug_and_counted(self):
        with mock.patch.object(
            notifications.urllib.request, "urlopen",
            side_effect=socket.timeout("timed out"),
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                result = notifications.poll_telegram_updates()
        self.assertEqual(result, [])
        self.assertIn("[DEBUG]", err.getvalue())
        state = notifications.get_telegram_poll_state()
        self.assertEqual(state["last_error_kind"], "timeout")
        self.assertEqual(state["consecutive_failures"], 1)

    def test_http_4xx_logged_once(self):
        exc = urllib.error.HTTPError(
            "http://example.invalid", 400, "Bad Request", {}, io.BytesIO(b'{"ok":false}')
        )
        with mock.patch.object(
            notifications.urllib.request, "urlopen", side_effect=exc
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                notifications.poll_telegram_updates()
                notifications.poll_telegram_updates()
        self.assertEqual(err.getvalue().count("Telegram 轮询 HTTP 400"), 1)
        self.assertEqual(
            notifications.get_telegram_poll_state()["consecutive_failures"], 2
        )

    def test_parse_error_includes_raw_prefix(self):
        with mock.patch.object(
            notifications.urllib.request, "urlopen",
            return_value=_FakeResponse(b"not-json-payload"),
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                result = notifications.poll_telegram_updates()
        self.assertEqual(result, [])
        self.assertIn("解析失败", err.getvalue())
        self.assertIn("not-json-payload", err.getvalue())

    def test_consecutive_failures_warn_once(self):
        with mock.patch.object(
            notifications.urllib.request, "urlopen",
            side_effect=socket.timeout("timed out"),
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                for _ in range(5):
                    notifications.poll_telegram_updates()
        self.assertEqual(err.getvalue().count("[WARN]"), 1)
        self.assertEqual(
            notifications.get_telegram_poll_state()["consecutive_failures"], 5
        )


class TestReporterDiskPath(unittest.TestCase):
    def test_uses_configured_data_dir(self):
        tmp = tempfile.mkdtemp(prefix="aws_p2_data_")
        try:
            with open(os.path.join(tmp, "traffic_log.csv"), "wb") as fh:
                fh.write(b"x" * (2 * 1024 * 1024))
            with mock.patch.object(reporter, "DATA_DIR", tmp), \
                 mock.patch.object(reporter, "DISK_ALERT_MB", 1), \
                 mock.patch.object(reporter.shutil, "disk_usage",
                                   return_value=types.SimpleNamespace(free=10 ** 12)):
                alerts = reporter._disk_alerts()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertTrue(any("磁盘 2.0MB" in a for a in alerts))

    def test_missing_dir_falls_back_with_warning(self):
        missing = os.path.join(tempfile.gettempdir(), "aws_p2_missing_dir_xyz")
        with mock.patch.object(reporter, "DATA_DIR", missing), \
             mock.patch.object(reporter.shutil, "disk_usage",
                               return_value=types.SimpleNamespace(free=10 ** 12)):
            err = io.StringIO()
            with redirect_stderr(err):
                alerts = reporter._disk_alerts()
        self.assertIsInstance(alerts, list)
        self.assertIn("不存在", err.getvalue())


class TestSystemctlResult(unittest.TestCase):
    def test_nonzero_returns_false_and_logs(self):
        proc = mock.Mock(returncode=1, stderr="boom", stdout="")
        with mock.patch.object(main.subprocess, "run", return_value=proc):
            err = io.StringIO()
            with redirect_stderr(err):
                ok, reason = main.run_systemctl(["restart", "x.service"])
        self.assertFalse(ok)
        self.assertIn("boom", reason)
        self.assertIn("失败", err.getvalue())

    def test_boot_action_reports_failures(self):
        proc = mock.Mock(returncode=1, stderr="denied", stdout="")
        with mock.patch.object(main.subprocess, "run", return_value=proc):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                failed = main._apply_boot_action([("x.service", "X")], "enable")
        self.assertEqual(len(failed), 1)
        self.assertIn("denied", failed[0][1])

    def test_timer_failure_restores_old_file(self):
        tmp = tempfile.mkdtemp(prefix="aws_p2_timer_")
        timer_path = os.path.join(tmp, "daily.timer")
        try:
            with open(timer_path, "w", encoding="utf-8") as fh:
                fh.write("OLD TIMER\n")
            with mock.patch.object(main, "run_systemctl", return_value=(False, "nope")):
                ok, reason = main._update_daily_report_timer(
                    "12:30", timer_path=timer_path
                )
            with open(timer_path, encoding="utf-8") as fh:
                content = fh.read()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(ok)
        self.assertIn("失败", reason)
        self.assertEqual(content, "OLD TIMER\n")

    def test_boot_toggle_source_does_not_print_success_on_failure(self):
        with open(os.path.join(MONITOR_DIR, "main.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("if failed:", source)
        self.assertIn("未全部", source)


class TestTelegramAuth(unittest.TestCase):
    def test_empty_chat_id_fails_closed(self):
        old = main.TG_CHAT_ID
        main.TG_CHAT_ID = ""
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                ok, reason = main._tg_callback_authorized("123")
        finally:
            main.TG_CHAT_ID = old
        self.assertFalse(ok)
        self.assertIn("未配置", reason)
        self.assertIn("ERROR", err.getvalue())

    def test_chat_id_must_match(self):
        old = main.TG_CHAT_ID
        main.TG_CHAT_ID = "123"
        try:
            self.assertTrue(main._tg_callback_authorized("123")[0])
            self.assertFalse(main._tg_callback_authorized("124")[0])
        finally:
            main.TG_CHAT_ID = old

    def test_uses_constant_time_compare(self):
        with open(os.path.join(MONITOR_DIR, "main.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("hmac.compare_digest", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
