"""Merge remote and just-delivered state before the scheduled job commits it."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arxiv_digest.state import load_state, merge_states, save_state

_DEFAULT_TIMEZONE = "Asia/Shanghai"


def _business_window(report_path: str) -> tuple[date | None, str]:
    """从 run_report.json 取业务日期/时区；缺失或损坏时返回 (None, "")，交给调用方兜底。

    run_report 只是 best-effort 诊断文件：写盘失败时邮件仍然可能已经发出。
    这时必须照常合并并提交 state，否则下一次运行会把同一批论文再推一遍。
    """
    if not report_path:
        return None, ""
    path = Path(report_path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        return date.fromisoformat(str(report["date"])), str(report.get("timezone") or "")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[WARN] 无法从 {path} 读取业务日期（{type(exc).__name__}），改用当前日期兜底。",
              file=sys.stderr)
        return None, ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("remote")
    parser.add_argument("local")
    parser.add_argument("output")
    parser.add_argument("--report", help="成功投递产生的 run_report.json，提供业务日期与时区")
    parser.add_argument("--today", help="report 不可用时的业务日期（YYYY-MM-DD），默认按当前时区取今天")
    parser.add_argument("--timezone", help="report 不可用时的时区，默认取 DIGEST_TIMEZONE 或 Asia/Shanghai")
    args = parser.parse_args(argv)
    today, timezone_name = _business_window(args.report)
    if today is None:
        timezone_name = args.timezone or os.getenv("DIGEST_TIMEZONE") or _DEFAULT_TIMEZONE
        try:
            today = date.fromisoformat(args.today) if args.today else datetime.now(ZoneInfo(timezone_name)).date()
        except (ValueError, KeyError):
            print(f"[WARN] 时区/日期 {timezone_name!r}/{args.today!r} 不可用，回退 UTC。", file=sys.stderr)
            timezone_name, today = "UTC", datetime.now(ZoneInfo("UTC")).date()
    merged = merge_states(load_state(args.remote), load_state(args.local), today=today, timezone_name=timezone_name)
    return 0 if save_state(merged, args.output) else 1


if __name__ == "__main__":
    raise SystemExit(main())
