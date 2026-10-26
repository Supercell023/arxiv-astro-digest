"""Offline observations for the 2026-10-26 reassessment; this is not a passing test suite.

Run from the repository: python -B docs/review_assets/post_fix_probes.py
Network is blocked by tests/__init__.py. All writable paths are temporary.
Exit code 0 means the probes completed, including observations of defects.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import logging
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.test_regressions import FakeClient, WorkspaceCase, paper
from arxiv_digest import main as entry, pipeline
from arxiv_digest.analysis import analyze_paper
from arxiv_digest.arxiv_client import ArxivRateLimitError
from arxiv_digest.emailer import render_email_html
from arxiv_digest.profile import Profile
from arxiv_digest.pulse import Material, TopicHit, compose_pulse
from arxiv_digest.radar import Radar, RadarBlock, RadarHit
from arxiv_digest.state import State, load_state, merge_states, prune, weekly_trend
from scripts.eval_analysis import check


def run() -> dict:
    case = WorkspaceCase()
    case.setUp()
    results = {}
    try:
        cfg = case.config
        hit = RadarHit("test", "Radar-only discovery", "https://example.invalid/r",
                       "astro-ph.HE", "2026-10-26", "review", id="2610.98765")
        radar = Radar(blocks=(RadarBlock("test", headline=hit, total=1),))
        with case.collected([]), patch.object(pipeline, "_build_radar", return_value=radar), \
                patch.object(entry, "send_email") as smtp:
            entry.main(replace(cfg, radar_enabled=True))
        snapshot = json.loads(Path(cfg.digest_json_path).read_text(encoding="utf-8"))
        results["R01"] = {"smtp_calls": smtp.call_count,
                          "seen_without_delivery": "2610.98765" in load_state(cfg.state_path).seen,
                          "snapshot_has_radar": bool(snapshot.get("radar"))}

        client = FakeClient([
            {"result": {"value": "42 clusters", "quote": "We measure 42 clusters."}},
            {"result": "42 clusters", "for_you": "复用论文测得的 999 clusters 结论",
             "actions": ["引用论文测得 999 clusters 的结果"], "chain": ["We measure 999 clusters."],
             "evidence": ["We measure 42 clusters."]},
        ])
        assessed = analyze_paper(client, paper(), Profile(), [], extract_model="fake", assess_model="fake")
        html = render_email_html("audit", [assessed])
        results["R02"] = {"source_has_999": "999" in assessed.analysis.source_text,
                          "validated_result": assessed.analysis.result,
                          "for_you": assessed.analysis.for_you, "actions": assessed.analysis.actions,
                          "chain": assessed.analysis.chain, "rendered_999_count": html.count("999")}

        remote = State(seen={"old": "2025-01-01", "recent": "2026-10-26"},
                       materials={"old": {"id": "old", "kind": "paper", "title": "Old",
                                          "source": "test", "date": "2025-01-01"}},
                       observations={"old": {"day": "2025-01-01", "category": "astro-ph.CO"}})
        local = copy.deepcopy(remote)
        prune(local, today=date(2026, 10, 26))
        merged = merge_states(remote, local)
        large_remote = State(seen={str(i): "2026-10-26" for i in range(5001)})
        large_local = copy.deepcopy(large_remote)
        prune(large_local, today=date(2026, 10, 26))
        results["R03"] = {"local_old_seen": "old" in local.seen, "merged_old_seen": "old" in merged.seen,
                          "local_old_material": "old" in local.materials,
                          "merged_old_material": "old" in merged.materials,
                          "merged_old_observation": "old" in merged.observations,
                          "capped_local_seen": len(large_local.seen),
                          "merged_seen": len(merge_states(large_remote, large_local).seen)}

        material = Material("p1", "paper", "Weak lensing", "test", "2026-10-26",
                            "lensing", "https://example.invalid/p1")
        topic = TopicHit("lensing", 2, ("test",), ("p1",))
        try:
            pulse = compose_pulse(FakeClient([{"headline": "valid", "headline_evidence": 42}]),
                                  "fake", [material], [topic], Profile())
            results["R04"] = {"exception": None, "basis": pulse.basis}
        except Exception as exc:
            results["R04"] = {"exception": type(exc).__name__, "message": str(exc)}

        trends = weekly_trend(State(daily={"2026-10-18": {"astro-ph.CO": 10},
                                          "2026-10-26": {"astro-ph.GA": 2}}), today=date(2026, 10, 26))
        results["R05"] = {"last_week_CO": 10, "this_week_CO": 0,
                          "returned_labels": [row.label for row in trends]}

        try:
            with patch.object(entry, "load_config", return_value=replace(cfg, arxiv_rate_limit_graceful=True)), \
                    case.collected([]), \
                    patch.object(pipeline, "_fetch_with_adaptive_window",
                                 side_effect=ArxivRateLimitError("simulated 429")), \
                    patch.object(entry, "notify_failure") as notify:
                code = entry.run_cli([])
                results["R06"] = {"returned": code, "notify_calls": notify.call_count}
        except Exception as exc:
            results["R06"] = {"exception": type(exc).__name__, "notify_calls": notify.call_count,
                              "config_graceful": True}

        cfg = replace(cfg, max_papers=1, deep_papers=1, brief_papers=0,
                      state_path=str(case.root / "race" / "state.json"),
                      feedback_path=str(case.root / "race" / "feedback.json"),
                      digest_json_path=str(case.root / "race" / "digest.json"),
                      run_report_path=str(case.root / "race" / "run.json"))

        def during_send(*args, **kwargs):
            entry.main(cfg, out=str(case.root / "other-preview.html"))

        with patch.object(pipeline, "_fetch_with_adaptive_window",
                          side_effect=[([paper("2610.11111")], 7), ([paper("2610.22222")], 7)]), \
                patch.object(pipeline, "_load_library", return_value=[]), \
                patch.object(pipeline, "_digest_today", return_value=date(2026, 10, 26)), \
                patch.object(entry, "send_email", side_effect=during_send):
            entry.main(cfg)
        snapshot = json.loads(Path(cfg.digest_json_path).read_text(encoding="utf-8"))
        results["R07"] = {"snapshot_item": snapshot["items"][0]["id"],
                          "snapshot_delivery": snapshot["run"]["delivery"],
                          "actual_seen": sorted(load_state(cfg.state_path).seen)}

        eval_path, digest_path = case.root / "eval.json", case.root / "eval-digest.json"
        eval_path.write_text(json.dumps({"entries": [{"id": str(i)} for i in range(20)]}), encoding="utf-8")
        digest_path.write_text(json.dumps({"items": [{"id": str(i), "summary": "42 clusters",
            "analysis": {"facts": {"result": {"value": "42 clusters", "quote": "42 clusters"}}}}
            for i in range(4)]}), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = check(eval_path, digest_path)
        results["R08"] = {"exit_code": code, "output": output.getvalue()}
        return results
    finally:
        case.doCleanups()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.disable(logging.CRITICAL)
    print(json.dumps({"network": "blocked", "writes": "temporary directories only",
                      "observations": run()}, ensure_ascii=False, indent=2))
