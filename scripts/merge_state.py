"""Merge remote and just-delivered state before the scheduled job commits it."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arxiv_digest.state import load_state, merge_states, save_state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("remote")
    parser.add_argument("local")
    parser.add_argument("output")
    parser.add_argument("--report", help="成功投递产生的 run_report.json，提供业务日期与时区")
    args = parser.parse_args(argv)
    today, timezone_name = None, "UTC"
    if args.report:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        today = date.fromisoformat(report["date"])
        timezone_name = report["timezone"]
    merged = merge_states(load_state(args.remote), load_state(args.local), today=today, timezone_name=timezone_name)
    return 0 if save_state(merged, args.output) else 1


if __name__ == "__main__":
    raise SystemExit(main())
