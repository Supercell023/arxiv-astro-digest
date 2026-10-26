"""Offline acceptance tests for weekly editions, broad news and the reassessment defects."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import threading
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from tests.test_regressions import WorkspaceCase, FakeClient, paper
from arxiv_digest import pipeline, main as entry
from arxiv_digest.analysis import analyze_paper
from arxiv_digest.arxiv_client import ArxivRateLimitError, _previous_local_window
from arxiv_digest.contracts import paper_to_dict
from arxiv_digest.editorial import edition_for, in_period, news_counts, relevant_papers, select_news
from arxiv_digest.emailer import render_email_html
from arxiv_digest.evidence import advice_supported
from arxiv_digest.models import Analysis
from arxiv_digest.packs import Pack
from arxiv_digest.profile import Profile
from arxiv_digest.pulse import Material, TopicHit, compose_pulse
from arxiv_digest.radar import Radar, RadarBlock, RadarHit
from arxiv_digest.sources import Item, SourceResult
from arxiv_digest.state import State, load_state, merge_states, prune, save_state, weekly_trend
from arxiv_digest.translate import translate_items
from scripts.eval_analysis import run_set, check


def news(identifier, domain="astronomy", stamp="2026-10-26T00:00:00+00:00", **changes):
    values = dict(id=identifier, kind="secondary", title=f"{domain} discovery {identifier}",
                  url=f"https://example.invalid/{identifier}", source=f"{domain} source", published=stamp,
                  text="Scientists measured 42 objects.", meta={"domain": domain})
    values.update(changes)
    return Item(**values)


class WeeklyEditionTests(WorkspaceCase):
    @contextlib.contextmanager
    def weekly(self, papers):
        with patch.object(pipeline, "_fetch_weekly_window", return_value=(papers, 7)), \
                patch.object(pipeline, "_load_library", return_value=[]), \
                patch.object(pipeline, "_digest_today", return_value=date(2026, 10, 25)):
            yield

    def test_auto_sunday_and_explicit_override(self):
        self.assertEqual(edition_for(self.config, date(2026, 10, 25)).kind, "weekly")
        self.assertEqual(edition_for(self.config, date(2026, 10, 26)).kind, "daily")
        self.assertEqual(edition_for(replace(self.config, digest_edition="daily"), date(2026, 10, 25)).kind, "daily")
        self.assertEqual(edition_for(replace(self.config, weekly_enabled=False), date(2026, 10, 25)).kind, "daily")

    def test_week_is_exact_seven_complete_local_days(self):
        edition = edition_for(self.config, date(2026, 10, 25))
        self.assertEqual(edition.period_label, "2026-10-18 至 2026-10-24")
        self.assertTrue(in_period(paper(day=18), edition, "Asia/Shanghai"))
        self.assertFalse(in_period(paper(day=25), edition, "Asia/Shanghai"))
        begin, end = _previous_local_window(7, "Asia/Shanghai", date(2026, 10, 25))
        self.assertEqual(begin.isoformat(), "2026-10-17T16:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-10-24T16:00:00+00:00")

    def test_weekly_fetch_uses_explicit_end_and_larger_budget_once(self):
        with patch.object(pipeline, "fetch_recent_papers", return_value=[]) as fetch:
            pipeline._fetch_weekly_window(self.config, date(2026, 10, 25))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.kwargs["end_date"], date(2026, 10, 25))
        self.assertEqual(fetch.call_args.kwargs["max_results"], 600)
        self.assertEqual(fetch.call_args.kwargs["max_pages"], 24)

    def test_weekly_reincludes_daily_sent_but_excludes_irrelevant_and_outside_dates(self):
        sent = paper(day=20)
        save_state(State(seen={"2610.12345": "2026-10-21"}), self.config.state_path)
        off = paper("2610.99990", title="Pulsar timing", summary="An isolated neutron star spins rapidly.")
        old = paper("2610.99991", day=17)
        future = paper("2610.99992", day=25)
        with self.weekly([sent, off, old, future]):
            result = pipeline.run_pipeline(self.config)
        self.assertEqual([p.id for p in result.enriched], [sent.id])
        self.assertEqual(result.report["edition"], "weekly")
        self.assertEqual(result.report["deep_papers"], 6)
        self.assertEqual(result.window_days, 7)
        self.assertFalse(load_state(self.config.state_path).editions)

    def test_weekly_delivery_once_and_no_daily_seen_consumption(self):
        with self.weekly([paper()]), patch.object(entry, "send_email") as smtp:
            entry.main(self.config)
            entry.main(self.config)
        self.assertEqual(smtp.call_count, 1)
        self.assertIn("Weekly Research", smtp.call_args.args[6])
        self.assertIn("本周研究精选", smtp.call_args.args[7])
        state = load_state(self.config.state_path)
        self.assertEqual(state.editions, {"weekly:2026-10-18:2026-10-25": "2026-10-25"})
        self.assertNotIn("2610.12345", state.seen)

    def test_duplicate_weekly_skips_fetch_and_model_work(self):
        save_state(State(editions={"weekly:2026-10-18:2026-10-25": "2026-10-25"}), self.config.state_path)
        with self.weekly([paper()]), patch.object(pipeline, "_fetch_weekly_window") as fetch:
            result = pipeline.run_pipeline(self.config)
        fetch.assert_not_called()
        self.assertEqual(result.report["skip_reason"], "weekly_already_delivered")

    def test_weekly_preview_can_replay_after_delivery_without_consumption(self):
        save_state(State(editions={"weekly:2026-10-18:2026-10-25": "2026-10-25"}), self.config.state_path)
        before = Path(self.config.state_path).read_bytes()
        with self.weekly([paper()]), patch.object(entry, "send_email") as smtp:
            entry.main(self.config, out=str(self.root / "weekly.html"))
        smtp.assert_not_called()
        self.assertEqual(Path(self.config.state_path).read_bytes(), before)
        self.assertIn("本周研究精选", (self.root / "weekly.html").read_text(encoding="utf-8"))

    def test_weekly_smtp_failure_does_not_consume_edition(self):
        with self.weekly([paper()]), patch.object(entry, "send_email", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                entry.main(self.config)
        self.assertFalse(load_state(self.config.state_path).editions)

    def test_preview_helper_respects_configured_weekly_mode_without_cli_override(self):
        from scripts import preview_digest
        config = replace(self.config, digest_edition="weekly")
        with self.collected(), patch.object(pipeline, "_fetch_weekly_window", return_value=([paper()], 7)), \
                patch.object(preview_digest, "load_config", return_value=config), \
                contextlib.redirect_stdout(io.StringIO()):
            preview_digest.main(["--out", str(self.root / "helper.html")])
        self.assertIn("本周研究精选", (self.root / "helper.html").read_text(encoding="utf-8"))
        self.assertFalse(Path(self.config.state_path).exists())

    def test_replay_preserves_weekly_header_and_layout_counts(self):
        from scripts import build_preview
        with self.weekly([paper(f"2610.{14000+i}") for i in range(7)]):
            pipeline.run_pipeline(self.config)
        with patch.object(build_preview, "load_config", return_value=self.config), \
                contextlib.redirect_stdout(io.StringIO()):
            build_preview.render(Path(self.config.digest_json_path), self.root / "replay.html")
        html = (self.root / "replay.html").read_text(encoding="utf-8")
        self.assertIn("本周研究精选", html)
        self.assertIn("精读 · 6 篇", html)
        self.assertIn("速览 · 1 篇", html)

    def test_daily_filters_weak_relevance_instead_of_padding(self):
        off = paper("2610.99990", title="Pulsar timing", summary="An isolated neutron star spins rapidly.")
        with self.collected([off, paper()]):
            result = pipeline.run_pipeline(self.config)
        self.assertEqual(len(result.enriched), 1)
        self.assertTrue(any("兴趣匹配" in r for r in result.enriched[0].score_reasons))

    def test_model_off_topic_paper_is_removed_and_not_consumed(self):
        papers = [paper("2610.11111"), paper("2610.22222")]
        def analyzed(config, items, **kwargs):
            return [replace(item, analysis=Analysis(relevance_kind="off" if index == 0 else "direct"))
                    for index, item in enumerate(items)]
        with self.collected(papers), patch.object(pipeline, "_maybe_analyze", side_effect=analyzed), \
                patch.object(entry, "send_email") as smtp:
            entry.main(self.config)
        self.assertEqual(set(load_state(self.config.state_path).seen), {"2610.22222"})
        self.assertIn("(1 papers)", smtp.call_args.args[6])

    def test_weekly_multiple_mentions_of_one_topic_do_not_make_two_signals(self):
        pack = Pack("test", journal_terms=("weak lensing", "weak-lensing", "cosmic shear"))
        one = paper(title="Study", summary="weak lensing and weak-lensing")
        two = paper("2610.12346", title="Study", summary="weak lensing and cosmic shear")
        picked = relevant_papers([one, two], [], [pack], Profile(), min_signals=2, strict=True)
        self.assertEqual([p.id for p in picked], [two.id])

    def test_explicit_interest_can_select_single_strong_weekly_signal(self):
        item = paper(title="Study", summary="An exotic probe for testing gravity")
        self.assertEqual(len(relevant_papers([item], ["exotic probe"], [], Profile(), min_signals=2, strict=True)), 1)

    def test_config_rejects_impossible_quotas(self):
        for changes in ({"weekly_weekday": 7}, {"world_astronomy_percent": 101},
                        {"weekly_deep_papers": 13}, {"world_news_max_items": 5}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.config, **changes)


class BroadNewsTests(WorkspaceCase):
    def select(self, items, **options):
        return select_news(items, today=date(2026, 10, 26), timezone_name="Asia/Shanghai", **options)

    def test_roughly_thirty_with_equal_halves_and_other_field_diversity(self):
        items = [news(f"a{i}") for i in range(30)]
        items += [news(f"{domain}{i}", domain) for domain in ("physics", "chemistry", "history") for i in range(10)]
        chosen = self.select(items)
        self.assertEqual(len(chosen), 30)
        self.assertEqual(news_counts(chosen), {"天文学": 15, "物理学": 5, "化学": 5, "历史与考古": 5})

    def test_abundant_inventory_can_grow_but_is_bounded(self):
        items = [news(f"{domain}{i}", domain) for domain in ("astronomy", "physics", "chemistry", "history") for i in range(40)]
        chosen = self.select(items)
        self.assertEqual(len(chosen), 36)
        self.assertEqual(news_counts(chosen)["天文学"], 18)

    def test_shortage_backfills_without_fake_or_repeated_items(self):
        chosen = self.select([news("a")] + [news(f"p{i}", "physics") for i in range(11)])
        self.assertEqual(len(chosen), 12)
        self.assertEqual(len({item.id for item in chosen}), 12)
        self.assertEqual(news_counts(chosen), {"天文学": 1, "物理学": 11})
        self.assertEqual(self.select([]), [])

    def test_stale_future_and_undated_are_not_promoted_as_current_news(self):
        items = [news("old", stamp="2026-10-18T00:00:00Z"), news("future", stamp="2026-10-27T00:00:00Z"),
                 news("undated", stamp=""), news("current")]
        self.assertEqual([item.id for item in self.select(items)], ["current"])

    def test_date_filter_uses_configured_timezone(self):
        chosen = self.select([news("boundary", stamp="2026-10-19T16:00:00Z")])
        self.assertEqual(len(chosen), 1)

    def test_cross_feed_same_link_and_title_are_deduplicated(self):
        same_url = news("b", "physics", url="https://example.invalid/a#top")
        same_title = news("c", "chemistry", title="astronomy discovery a")
        chosen = self.select([news("a"), same_url, same_title])
        self.assertEqual(len(chosen), 1)

    def test_stale_long_description_cannot_displace_fresh_duplicate(self):
        stale = news("stale", stamp="2026-10-10T00:00:00Z", title="Shared story", text="old " * 100)
        fresh = news("fresh", title="Shared story", text="current")
        self.assertEqual([item.id for item in self.select([stale, fresh])], ["fresh"])

    def test_tracking_links_and_reused_ids_do_not_duplicate_news(self):
        items = [news("a", url="https://example.invalid/story?utm_source=one"),
                 news("b", url="https://example.invalid/story?utm_source=two"),
                 news("a", title="Revised headline", url="https://example.invalid/other")]
        self.assertEqual(len(self.select(items)), 1)

    def test_world_selection_is_used_by_real_delivery_and_only_selected_are_seen(self):
        items = [news(f"{domain}{i}", domain) for domain in ("astronomy", "physics", "chemistry", "history") for i in range(15)]
        config = replace(self.config, sources_enabled=True)
        with self.collected([]), patch.object(pipeline, "source_ids", return_value={}), \
                patch.object(pipeline, "collect_sources", return_value=[SourceResult("News", items=items)]), \
                patch.object(entry, "send_email") as smtp:
            entry.main(config)
        self.assertEqual(len(load_state(config.state_path).seen), 30)
        body = smtp.call_args.args[7]
        self.assertIn("世界在变", body)
        self.assertIn("天文学 15 条", body)
        self.assertIn("历史与考古 5 条", body)

    def test_non_astro_news_does_not_dilute_astronomy_pulse_materials(self):
        state = State()
        materials = pipeline._remember_materials(state, [], [SourceResult("News", items=[news("astro"), news("history", "history")])], date(2026, 10, 26))
        self.assertEqual([m.id for m in materials], ["astro"])

    def test_translation_rejects_unsupported_numbers_and_preserves_english(self):
        item = news("p", "chemistry")
        client = FakeClient([{"items": [{"id": "p", "title_zh": "发现 999 个样本", "text_zh": "测得 999 个样本"}]}])
        translated = translate_items(client, "fake", [item])[0]
        self.assertEqual(translated.title, item.title)
        self.assertFalse(translated.title_zh)
        self.assertFalse(translated.text_zh)


class ReassessmentFixTests(WorkspaceCase):
    def radar(self):
        hit = RadarHit("test", "Discovery", "https://example.invalid/r", "astro-ph.HE", "2026-10-26", "why", id="2610.98765")
        return Radar(blocks=(RadarBlock("test", headline=hit, total=1),))

    def test_radar_only_is_delivered_before_consumption(self):
        def sending(*args, **kwargs):
            self.assertNotIn("2610.98765", load_state(self.config.state_path).seen)
        with self.collected([]), patch.object(pipeline, "_build_radar", return_value=self.radar()), \
                patch.object(entry, "send_email", side_effect=sending) as smtp:
            entry.main(self.config)
        smtp.assert_called_once()
        self.assertIn("2610.98765", load_state(self.config.state_path).seen)

    def test_radar_only_failed_delivery_does_not_consume(self):
        with self.collected([]), patch.object(pipeline, "_build_radar", return_value=self.radar()), \
                patch.object(entry, "send_email", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                entry.main(self.config)
        self.assertFalse(load_state(self.config.state_path).seen)

    def test_all_visible_assessment_paths_reject_unsupported_numeric_claims(self):
        payload = {key: "论文测得 999 clusters" for key in ("for_you", "read_reason", "relevance_reason")}
        payload.update(result="42 clusters", actions=["引用测得 999 clusters 的结果", "建议抽查 3 篇"],
                       chain=["测得 999 clusters"], evidence=["We measure 42 clusters."],
                       red_flags=[{"flag": "test", "status": "discussed", "note": "999 clusters"},
                                  {"flag": "论文测得 999 clusters", "status": "discussed", "note": "原文已讨论"}],
                       concepts=[{"term": "test", "gloss": "999 clusters"}])
        client = FakeClient([{"result": {"value": "42 clusters", "quote": "We measure 42 clusters."},
                             "numbers": [{"label": "论文测得 999 clusters", "value": "42", "quote": "We measure 42 clusters."}]}, payload])
        result = analyze_paper(client, paper(chinese_summary="论文测得 999 clusters"), Profile(), [], extract_model="fake", assess_model="fake")
        self.assertNotIn("999", render_email_html("audit", [result]))
        self.assertEqual(result.analysis.actions, ("建议抽查 3 篇",))
        self.assertEqual(result.analysis.result, "42 clusters")

    def test_suggestion_quantity_exemption_cannot_hide_later_measurement(self):
        self.assertTrue(advice_supported("建议抽查 3 篇", "42 clusters"))
        self.assertFalse(advice_supported("建议抽查 3 篇，论文测得 999 clusters", "42 clusters"))
        self.assertFalse(advice_supported("建议引用论文测得 999 clusters 的结论", "42 clusters"))

    def test_merge_preserves_valid_editions_and_retention_invariants(self):
        remote = State(seen={str(i): "2026-10-26" for i in range(5001)},
            materials={"old": {"id": "old", "kind": "paper", "title": "old", "source": "test", "date": "2025-01-01"}},
            observations={"old": {"day": "2025-01-01", "category": "CO"}},
            editions={"weekly:old": "2025-01-01", "weekly:valid": "2026-10-26"})
        local = copy.deepcopy(remote)
        prune(local, today=date(2026, 10, 26))
        merged = merge_states(remote, local, today=date(2026, 10, 26), timezone_name="Asia/Shanghai")
        self.assertEqual(len(merged.seen), 5000)
        self.assertFalse(merged.materials)
        self.assertFalse(merged.observations)
        self.assertEqual(merged.editions, {"weekly:valid": "2026-10-26"})
        self.assertEqual(merge_states(merged, merged, today=date(2026, 10, 26)).__dict__, merged.__dict__)

    def test_bad_pulse_types_fall_back(self):
        material = Material("p", "paper", "Weak lensing", "test", "2026-10-26", "lensing", "https://example.invalid/p")
        topic = TopicHit("lensing", 2, ("test",), ("p",))
        for raw in (42, "p", {"p": True}, None):
            with self.subTest(raw=raw):
                result = compose_pulse(FakeClient([{"headline": "x", "headline_evidence": raw}]), "fake", [material], [topic], Profile())
                self.assertEqual(result.basis, "fallback")

    def test_bad_pulse_type_does_not_abort_actual_pipeline(self):
        material = Material("p", "paper", "Weak lensing", "test", "2026-10-26", "lensing", "https://example.invalid/p")
        topic = TopicHit("lensing", 2, ("test",), ("p",))
        with self.collected(), patch.object(pipeline, "_build_client", return_value=FakeClient([{"headline_evidence": 42}])), \
                patch.object(pipeline, "_remember_materials", return_value=[material]), \
                patch.object(pipeline, "count_topics", return_value=[topic]):
            result = pipeline.run_pipeline(replace(self.config, pulse_enabled=True))
        self.assertEqual(result.pulse.basis, "fallback")
        self.assertEqual(len(result.enriched), 1)

    def test_decline_to_zero_is_in_sample_trend(self):
        rows = weekly_trend(State(daily={"2026-10-18": {"CO": 10}, "2026-10-26": {"GA": 2}}), today=date(2026, 10, 26))
        co = next(row for row in rows if row.label == "CO")
        self.assertEqual((co.this_week, co.delta_pct), (0, -100))

    def test_rate_limit_graceful_true_false_and_preview(self):
        for graceful, preview in ((True, False), (False, False), (True, True)):
            with self.subTest(graceful=graceful, preview=preview), self.collected([]), \
                    patch.object(entry, "load_config", return_value=replace(self.config, arxiv_rate_limit_graceful=graceful)), \
                    patch.object(pipeline, "_fetch_with_adaptive_window", side_effect=ArxivRateLimitError("429")), \
                    patch.object(entry, "notify_failure") as notify:
                args = ["--out", str(self.root / "p.html")] if preview else []
                if graceful and not preview:
                    self.assertEqual(entry.run_cli(args), 0)
                else:
                    with self.assertRaises(ArxivRateLimitError):
                        entry.run_cli(args)
                self.assertEqual(notify.call_count, 0 if preview else 1)

    def test_preview_interleave_keeps_delivered_snapshot_and_per_run_history(self):
        def during_send(*args, **kwargs):
            entry.main(self.config, out=str(self.root / "other.html"))
        with self.collected(), patch.object(pipeline, "_fetch_with_adaptive_window",
                side_effect=[([paper("2610.11111")], 7), ([paper("2610.22222")], 7)]), \
                patch.object(entry, "send_email", side_effect=during_send):
            entry.main(self.config)
        latest = json.loads(Path(self.config.digest_json_path).read_text(encoding="utf-8"))
        self.assertEqual(latest["items"][0]["id"], "2610.11111", "预览不得顶掉已投递的最近快照")
        self.assertEqual(latest["run"]["delivery"], "committed")
        runs = [json.loads(path.read_text(encoding="utf-8")) for path in (self.root / "runs").glob("digest-*.json")]
        self.assertEqual({row["items"][0]["id"]: row["run"]["delivery"] for row in runs},
                         {"2610.11111": "committed", "2610.22222": "preview"})

    def test_pipeline_failure_writes_safe_current_run_report(self):
        with patch.object(pipeline, "_fetch_with_adaptive_window", side_effect=OSError("private-secret")):
            with self.assertRaises(RuntimeError):
                pipeline.run_pipeline(self.config)
        text = Path(self.config.run_report_path).read_text(encoding="utf-8")
        self.assertNotIn("private-secret", text)
        self.assertEqual(json.loads(text)["delivery"], "failed")

    def test_render_failure_and_state_failure_are_diagnosable(self):
        with self.collected(), patch.object(entry, "render_email_html", side_effect=ValueError("bad template")):
            with self.assertRaises(ValueError):
                entry.main(self.config)
        report = json.loads(Path(self.config.run_report_path).read_text(encoding="utf-8"))
        self.assertEqual((report["delivery"], report["stage"]), ("failed", "render"))
        with self.collected(), patch.object(entry, "send_email"), patch.object(pipeline, "save_state", return_value=False):
            with self.assertRaises(OSError):
                entry.main(self.config)
        report = json.loads(Path(self.config.run_report_path).read_text(encoding="utf-8"))
        self.assertEqual(report["delivery"], "state_commit_failed")
        self.assertTrue(report["smtp_accepted"])

    def test_single_stage_summaries_reject_new_measurement_numbers(self):
        from arxiv_digest.llm import _call_batch
        item = paper()
        for text, accepted in (("测得 999 clusters", False), ("测得 42 clusters", True)):
            client = FakeClient([{"paper_summaries": {item.id: text}}])
            _, summaries = _call_batch(client, "fake", [item], ask_overall=False, max_abstract_chars=1800)
            self.assertEqual(item.id in summaries, accepted)

    def test_cache_maintenance_bounds_only_regenerable_files(self):
        from arxiv_digest.cache import prune_cache
        root = self.root / "cache" / "extract"
        root.mkdir(parents=True)
        now = 1800000000
        old, first, second = [root / (letter * 64 + ".json") for letter in ("a", "b", "c")]
        for path, stamp in ((old, now - 40 * 86400), (first, now - 20), (second, now - 10)):
            path.write_bytes(b"12345678")
            os.utime(path, (stamp, stamp))
        keep = root / "user-notes.json"
        keep.write_text("do not remove", encoding="utf-8")
        self.assertEqual(prune_cache(str(root.parent), now=now, max_bytes=10), 2)
        self.assertFalse(old.exists())
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue(keep.exists())

    def test_feedback_server_bounds_connections_and_releases_slots_on_errors(self):
        from scripts.feedback_server import FeedbackServer, ThreadingHTTPServer
        server = FeedbackServer.__new__(FeedbackServer)
        server._slots = threading.BoundedSemaphore(1)
        request = MagicMock()
        with patch.object(ThreadingHTTPServer, "process_request"), patch.object(server, "shutdown_request") as close:
            server.process_request(request, ("127.0.0.1", 1))
            request.settimeout.assert_called_once_with(10)
            server.process_request(MagicMock(), ("127.0.0.1", 2))
            close.assert_called_once()
        with patch.object(ThreadingHTTPServer, "process_request_thread", side_effect=ValueError("handler failed")):
            with self.assertRaises(ValueError):
                server.process_request_thread(request, ("127.0.0.1", 1))
        self.assertTrue(server._slots.acquire(blocking=False))

    def test_fixed_evaluation_runs_every_frozen_input_beyond_daily_quota(self):
        papers = [paper(f"2610.{12000+i}") for i in range(5)]
        path, output = self.root / "set.json", self.root / "evaluated.json"
        path.write_text(json.dumps({"entries": [{"id": p.id} for p in papers], "items": [paper_to_dict(p) for p in papers]}), encoding="utf-8")
        responses = []
        for _ in papers:
            responses.extend([{"result": {"value": "42 clusters", "quote": "We measure 42 clusters."}},
                              {"result": "42 clusters", "evidence": ["We measure 42 clusters."]}])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_set(path, output, client=FakeClient(responses), config=self.config), 0)
            self.assertEqual(check(path, output), 0)
        self.assertEqual(len(json.loads(output.read_text(encoding="utf-8"))["items"]), 5)

    def test_legacy_eval_set_fails_before_any_model_request(self):
        path = self.root / "legacy.json"
        path.write_text(json.dumps({"entries": [{"id": paper().id}]}), encoding="utf-8")
        client = FakeClient([])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_set(path, self.root / "out.json", client=client, config=self.config), 1)
        self.assertFalse(client.calls)

    def test_workflow_separates_verification_and_delivery_groups(self):
        import yaml
        workflow = yaml.load(Path(".github/workflows/daily-arxiv-digest.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        group = workflow["concurrency"]["group"]
        self.assertIn("digest-delivery", group)
        self.assertIn("digest-check-", group)
        uploads = next(step for step in workflow["jobs"]["send-digest"]["steps"] if step.get("uses", "").startswith("actions/upload-artifact"))
        self.assertIn("DIGEST_JSON_PATH", uploads["with"]["path"])
        self.assertIn("runs/", uploads["with"]["path"])
