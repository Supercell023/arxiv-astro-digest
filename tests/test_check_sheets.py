from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import tests  # noqa: F401 - 装上网络守卫

from arxiv_digest import archive
from scripts import check_sheets


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FeedbackCheckTests(unittest.TestCase):
    def test_header_and_rows_are_accepted(self):
        csv = "item,relevance,usefulness,t\n2610.12345,2,1,2026-10-28T12:00:00Z\n"
        ok, message = check_sheets.check_feedback("https://x", fetch=lambda url: csv)
        self.assertTrue(ok, message)
        self.assertIn("1 行投票", message)

    def test_wrong_tab_is_called_out(self):
        csv = "week_start,week_label,topic,votes\n2026-10-25,周,弱透镜,2\n"
        ok, message = check_sheets.check_feedback("https://x", fetch=lambda url: csv)
        self.assertFalse(ok)
        self.assertIn("热点档案", message)
        self.assertIn("gid", message)

    def test_unknown_header_is_reported(self):
        ok, message = check_sheets.check_feedback("https://x", fetch=lambda url: "a,b,c,d\n")
        self.assertFalse(ok)
        self.assertIn("表头应为", message)

    def test_html_response_is_rejected(self):
        with patch.object(check_sheets, "urlopen", return_value=FakeResponse(b"<html>login</html>")):
            with self.assertRaises(ValueError) as caught:
                check_sheets.fetch_csv("https://x")
        self.assertIn("HTML", str(caught.exception))

    def test_empty_response_is_rejected(self):
        with patch.object(check_sheets, "urlopen", return_value=FakeResponse(b"  \n")):
            with self.assertRaises(ValueError) as caught:
                check_sheets.fetch_csv("https://x")
        self.assertIn("空内容", str(caught.exception))


class ArchiveCheckTests(unittest.TestCase):
    def config(self, **values):
        base = dict(archive_enabled=True, archive_webhook_url="https://x/exec", archive_token="token",
                    archive_timeout=20, archive_max_rows=400)
        base.update(values)
        return SimpleNamespace(**base)

    def test_ready_endpoint_passes(self):
        class Healthy:
            def health(self):
                return {"ok": True}

        ok, message = check_sheets.check_archive(self.config(), transport=Healthy())
        self.assertTrue(ok, message)

    def test_unreachable_endpoint_fails(self):
        class Broken:
            def health(self):
                raise archive.ArchiveError("权限不足")

        ok, message = check_sheets.check_archive(self.config(), transport=Broken())
        self.assertFalse(ok)
        self.assertIn("权限不足", message)

    def test_unconfigured_endpoint_is_skipped(self):
        ok, message = check_sheets.check_archive(self.config(archive_webhook_url=""))
        self.assertTrue(ok)
        self.assertIn("跳过", message)


class MainTests(unittest.TestCase):
    def test_reports_nothing_configured(self):
        config = SimpleNamespace(archive_enabled=True, archive_webhook_url="", archive_token="",
                                 archive_timeout=20, archive_max_rows=400,
                                 feedback_sheet_csv_url="", feedback_url="", feedback_signing_key="")
        output = io.StringIO()
        with patch.object(check_sheets, "load_config", return_value=config), redirect_stdout(output):
            code = check_sheets.main([])
        self.assertEqual(code, 2)
        self.assertIn("无从检查", output.getvalue())

    def test_returns_one_when_a_check_fails(self):
        config = SimpleNamespace(archive_enabled=True, archive_webhook_url="https://x/exec",
                                 archive_token="token", archive_timeout=20, archive_max_rows=400,
                                 feedback_sheet_csv_url="https://x/csv", feedback_url="", feedback_signing_key="")

        class Broken:
            def health(self):
                raise archive.ArchiveError("连不上")

        output = io.StringIO()
        with patch.object(check_sheets, "load_config", return_value=config), \
                patch.object(check_sheets, "check_archive", return_value=(False, "连不上")), \
                patch.object(check_sheets, "check_feedback", return_value=(True, "表头正确，共 0 行投票")), \
                redirect_stdout(output):
            code = check_sheets.main([])
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]", output.getvalue())


if __name__ == "__main__":
    unittest.main()
