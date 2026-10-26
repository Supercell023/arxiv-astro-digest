from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
import tests  # ensure the network guard also runs under `unittest discover -s tests`
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from xml.etree.ElementTree import fromstring

from arxiv_digest import main as entry, pipeline
from arxiv_digest.analysis import _excerpt, _strip_missing_notes, analyze_paper, coerce_analysis, extract_bundle
from arxiv_digest.calendar import empty_bundle
from arxiv_digest.config import load_config
from arxiv_digest.contracts import paper_from_dict, paper_to_dict
from arxiv_digest.emailer import _feedback_links, _safe_url, send_email
from arxiv_digest.feedback import (FeedbackRecord, FeedbackStore, apply_feedback, combine_stores,
    learned_weights, load_feedback, merge_csv, parse_feedback_csv, record_vote, remember_papers)
from arxiv_digest.feedback_security import signed_params, verify_params
from arxiv_digest.identity import canonical_id
from arxiv_digest.journals import Journal, fetch_journal_papers
from arxiv_digest.llm_client import LLMOptions, call_json
from arxiv_digest.mathfmt import to_unicode
from arxiv_digest.models import Analysis, Fact, KeyNumber, Paper, ScoreBreakdown
from arxiv_digest.packs import Pack, dispatch
from arxiv_digest.profile import Profile
from arxiv_digest.pulse import Material, coerce_pulse, count_topics
from arxiv_digest.radar import Neighborhood, select_blocks
from arxiv_digest.sources import Item, SourceResult
from arxiv_digest.state import State, filter_unseen, load_state, mark_seen, record_observations, save_state


def paper(identifier="2610.12345v1", title="Weak lensing cluster sample", day=20, **changes):
    p = Paper(identifier, title, ["A. Test"], "We measure 42 clusters. The mass bias is 10 percent.",
              datetime(2026, 10, day, tzinfo=timezone.utc), datetime(2026, 10, day, tzinfo=timezone.utc),
              ["astro-ph.CO"], "https://example.invalid/p.pdf", "https://example.invalid/abs")
    return replace(p, **changes)


class FakeClient:
    def __init__(self, responses, base_url="https://api.openai.com"):
        self.responses = list(responses)
        self.calls = []
        self.base_url = base_url
        self.chat = self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)), finish_reason="stop")])


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="digest_regression_")
        self.root = Path(self.temp.name)
        with patch.dict(os.environ, {}, clear=True), patch("arxiv_digest.config._load_dotenv_if_available"):
            config = load_config()
        self.config = replace(config, openai_api_key=None, zotero_api_key=None, journals_enabled=False,
            sources_enabled=False, analysis_enabled=False, pulse_enabled=False, radar_enabled=False,
            calendar_enabled=False, translate_enabled=False, state_path=str(self.root / "state.json"),
            feedback_path=str(self.root / "feedback.json"), cache_dir=str(self.root / "cache"),
            run_report_path=str(self.root / "run.json"), digest_json_path=str(self.root / "digest.json"),
            smtp_host="smtp.invalid", smtp_username="test", smtp_password="dummy", email_from="a@test.invalid",
            email_to="b@test.invalid")
        self.addCleanup(self.temp.cleanup)

    @contextlib.contextmanager
    def collected(self, papers=None):
        with patch.object(pipeline, "_fetch_with_adaptive_window", return_value=(papers if papers is not None else [paper()], 7)), \
             patch.object(pipeline, "_load_library", return_value=[]), \
             patch.object(pipeline, "_digest_today", return_value=date(2026, 10, 26)):
            yield


class DeliveryTests(WorkspaceCase):
    def test_preview_never_consumes_state_or_sends(self):
        with self.collected(), patch.object(entry, "send_email") as smtp:
            entry.main(self.config, out=str(self.root / "preview.html"))
        self.assertFalse(Path(self.config.state_path).exists())
        self.assertTrue((self.root / "preview.html").is_file())
        smtp.assert_not_called()

    def test_preview_does_not_write_production_digest(self):
        with self.collected(), patch.object(entry, "send_email") as smtp:
            entry.main(self.config, out=str(self.root / "preview.html"))
        self.assertFalse((self.root / "digest.json").exists(), "预览不得覆盖反馈/评测共用的生产快照")
        report = json.loads((self.root / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(report["delivery"], "preview")
        smtp.assert_not_called()

    def test_analysis_failures_are_reported(self):
        config = replace(self.config, analysis_enabled=True, deep_papers=1)
        with self.collected(), \
                patch.object(pipeline, "_build_client", return_value=object()), \
                patch.object(pipeline, "_fetch_fulltexts", return_value={}), \
                patch.object(pipeline, "analyze_papers", side_effect=RuntimeError("model down")), \
                patch.object(entry, "send_email"):
            entry.main(config, out=str(self.root / "preview.html"))
        report = json.loads((self.root / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(report["analyzed_papers"], 0)
        self.assertEqual(report["analysis_failures"], 1)

    def test_failed_delivery_does_not_consume(self):
        with self.collected(), patch.object(entry, "send_email", side_effect=OSError("SMTP failed")):
            with self.assertRaises(OSError):
                entry.main(self.config)
        self.assertFalse(Path(self.config.state_path).exists())

    def test_render_failure_does_not_consume(self):
        with self.collected(), patch.object(entry, "render_email_html", side_effect=ValueError("bad template")):
            with self.assertRaises(ValueError):
                entry.main(self.config)
        self.assertFalse(Path(self.config.state_path).exists())

    def test_commit_happens_after_smtp(self):
        def sent(*args, **kwargs):
            self.assertFalse(Path(self.config.state_path).exists())
        with self.collected(), patch.object(entry, "send_email", side_effect=sent):
            entry.main(self.config)
        self.assertIn("2610.12345", load_state(self.config.state_path).seen)

    def test_repeat_run_excludes_sent_paper(self):
        with self.collected(), patch.object(entry, "send_email") as smtp:
            entry.main(self.config)
            entry.main(self.config)
        self.assertEqual(smtp.call_count, 1)

    def test_only_displayed_papers_consumed(self):
        config = replace(self.config, max_papers=1, deep_papers=1, brief_papers=0)
        with self.collected([paper(), paper("2610.12346v1", day=21)]), patch.object(entry, "send_email"):
            entry.main(config)
        self.assertEqual(len(load_state(config.state_path).seen), 1)

    def test_pipeline_cannot_implicitly_commit(self):
        with self.assertRaises(ValueError):
            pipeline.run_pipeline(self.config, persist_state=True)

    def test_complete_smtp_preflight(self):
        self.assertFalse(entry._smtp_ready(replace(self.config, smtp_password="")))

    def test_feedback_write_failure_does_not_undo_delivery_state(self):
        with self.collected(), patch.object(entry, "send_email"), patch.object(pipeline, "save_feedback", side_effect=OSError("full")):
            entry.main(self.config)
        self.assertIn("2610.12345", load_state(self.config.state_path).seen)

    def test_optional_diagnostic_failure_does_not_block_delivery(self):
        with self.collected(), patch.object(entry, "send_email") as smtp, patch.object(pipeline, "atomic_json", side_effect=OSError("full")):
            entry.main(self.config)
        smtp.assert_called_once()
        self.assertIn("2610.12345", load_state(self.config.state_path).seen)

    def test_all_primary_sources_failed_is_not_reported_as_empty_success(self):
        with patch.object(pipeline, "_fetch_with_adaptive_window", side_effect=OSError("offline")):
            with self.assertRaises(RuntimeError):
                pipeline.run_pipeline(self.config)

    def test_weekly_materials_survive_between_runs(self):
        config = replace(self.config, pulse_enabled=True)
        with self.collected([paper(day=21)]), patch.object(entry, "send_email"):
            entry.main(config)
        with self.collected([paper("2610.12346v1", day=26)]), \
             patch.object(pipeline, "_digest_today", return_value=date(2026, 10, 27)):
            result = pipeline.run_pipeline(config)
        self.assertEqual(result.pulse.materials, 2)

    def test_arxiv_failure_keeps_secondary_path(self):
        item = Item("story", "secondary", "Story", "https://example.invalid", "S")
        with patch.object(pipeline, "_fetch_with_adaptive_window", side_effect=OSError("offline")), \
             patch.object(pipeline, "_load_library", return_value=[]), \
             patch.object(pipeline, "_collect_world", return_value=([SourceResult("S", [item], key="s")], [item], [])):
            result = pipeline.run_pipeline(self.config)
        self.assertEqual(result.secondary[0].id, "story")
        self.assertTrue(result.report["errors"])


class StateTests(WorkspaceCase):
    def test_canonical_arxiv_and_doi_identity(self):
        self.assertEqual(canonical_id("2610.12345v2"), "2610.12345")
        self.assertEqual(canonical_id("doi:10.1234/ABC"), "doi:10.1234/abc")
        self.assertEqual(canonical_id("https://arxiv.org/pdf/2610.12345.pdf"), "2610.12345")
        state = State()
        mark_seen(state, [paper()], day="2026-10-26")
        self.assertEqual(filter_unseen(state, [paper("2610.12345v2")]), [])

    def test_rss_ids_stable_across_processes(self):
        script = ("from arxiv_digest.sources.rss import _entry_to_item; from xml.etree.ElementTree import fromstring;"
                  " print(_entry_to_item(fromstring('<item><title>T</title><link>https://example.invalid/a</link></item>'),'s','S',()).id)")
        ids = [subprocess.check_output([sys.executable, "-B", "-c", script], text=True,
                env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"}).strip()
               for seed in ("1", "2")]
        self.assertEqual(ids[0], ids[1])

    def test_observations_idempotent_and_date_based(self):
        state = State()
        for _ in range(2):
            record_observations(state, [paper(), paper("2610.12345v2")], timezone_name="Asia/Shanghai")
        self.assertEqual(state.daily, {"2026-10-20": {"astro-ph.CO": 1}})

    def test_corrupt_state_file_is_quarantined(self):
        Path(self.config.state_path).write_text("{not json", encoding="utf-8")
        state = load_state(self.config.state_path)
        self.assertEqual(state.seen, {})
        self.assertTrue(Path(self.config.state_path + ".corrupt").is_file(), "读坏的 state 应先留证据再降级")

    def test_bad_state_fields_do_not_crash(self):
        for raw in ({"version": "oops"}, {"version": 2, "daily": {"x": {"a": "oops"}}, "observations": []}):
            Path(self.config.state_path).write_text(json.dumps(raw), encoding="utf-8")
            self.assertIsInstance(load_state(self.config.state_path), State)

    def test_malformed_weekly_material_dropped(self):
        Path(self.config.state_path).write_text(json.dumps({"version": 2, "materials": {"bad": {"date": "2026-10-20"}}}), encoding="utf-8")
        self.assertFalse(load_state(self.config.state_path).materials)

    def test_v1_migration_keeps_seen_discards_untrustworthy_counts(self):
        Path(self.config.state_path).write_text(json.dumps({"version": 1, "seen": {"2610.12345v1": "2026-10-20"},
            "daily": {"2026-10-26": {"astro-ph.CO": 999}}}), encoding="utf-8")
        state = load_state(self.config.state_path)
        self.assertEqual(state.version, 2)
        self.assertIn("2610.12345", state.seen)
        self.assertFalse(state.daily)

    def test_concurrent_remote_state_merge_preserves_both_deliveries(self):
        from arxiv_digest.state import merge_states
        remote = State(seen={"remote": "2026-10-25"}, fetched={"source": "2026-10-26"})
        local = State(seen={"local": "2026-10-26"}, fetched={"source": "2026-10-25"})
        merged = merge_states(remote, local)
        self.assertEqual(set(merged.seen), {"remote", "local"})
        self.assertEqual(merged.fetched["source"], "2026-10-26")

    def test_failed_weekly_source_not_marked_fetched(self):
        state = State()
        with patch.object(pipeline, "source_ids", return_value={"s": "weekly"}), \
             patch.object(pipeline, "collect_sources", return_value=[SourceResult("S", error="timeout", key="s")]):
            pipeline._collect_world(replace(self.config, sources_enabled=True), state, "2026-10-26")
        self.assertNotIn("s", state.fetched)


class ContractAndFeedbackTests(WorkspaceCase):
    def test_lossless_snapshot_roundtrip(self):
        analysis = Analysis(result="42 clusters", facts={"result": Fact("42 clusters", "We measure 42 clusters.")},
            source_text=paper().summary, numbers=(KeyNumber("count", "42", "We measure 42 clusters."),),
            figures=("Figure 1",))
        p = paper(analysis=analysis, breakdown=ScoreBreakdown(keyword=1.2))
        self.assertEqual(paper_from_dict(json.loads(json.dumps(paper_to_dict(p)))), p)

    def test_export_consumed_by_eval_and_replay(self):
        from scripts.eval_analysis import check
        from scripts.build_preview import build_papers, render
        analysis = Analysis(result="42", facts={"result": Fact("42", "We measure 42 clusters.")},
            source_text=paper().summary, numbers=(KeyNumber("count", "42", "We measure 42 clusters."),))
        target = Path(self.config.digest_json_path)
        pipeline._write_digest_json(str(target), empty_bundle(), [paper(analysis=analysis)], "summary")
        spec = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(build_papers(spec)[0][0].authors, ["A. Test"])
        eval_path = self.root / "eval.json"
        eval_path.write_text(json.dumps({"entries": [{"id": paper().id, "expected_numbers": ["42"]}]}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), patch("scripts.build_preview.load_config", return_value=self.config):
            self.assertEqual(check(eval_path, target), 0)
            self.assertEqual(render(target, self.root / "replay.html", deep=1, brief=0), 0)

    def test_csv_learns_from_persisted_history(self):
        state = State()
        store = FeedbackStore()
        remember_papers(store, [paper()])
        state.feedback = store.to_dict()
        save_state(state, self.config.state_path)
        config = replace(self.config, feedback_sheet_csv_url="https://example.invalid/feedback")
        with self.collected(), patch.object(pipeline, "_fetch_sheet_csv", return_value="item,relevance,usefulness\n2610.12345v1,2,2\n"):
            result = pipeline.run_pipeline(config)
        from arxiv_digest.feedback import store_from_dict
        learned = store_from_dict(result.pending_state.feedback)
        self.assertTrue(learned_weights(learned)[0])
        self.assertGreater(result.ranked[0].breakdown.feedback, 0)
        pipeline.commit_delivery(config, result)
        self.assertTrue(load_feedback(config.feedback_path).votes)
        self.assertFalse(load_state(config.state_path).feedback["votes"], "public state must not contain personal votes")

    def test_score_and_rating_stay_consistent(self):
        store = FeedbackStore(votes={"old": FeedbackRecord(2, 2)},
            history={"old": {"categories": ["astro-ph.CO"], "authors": ["A. Test"]}})
        adjusted = apply_feedback(store, [paper(score=0.4, rating=2)])[0]
        self.assertEqual(adjusted.rating, 3)
        self.assertEqual(adjusted.score, 1.35)

    def test_recording_without_metadata_preserves_previous_history(self):
        store = FeedbackStore()
        remember_papers(store, [paper()])
        record_vote(store, paper(title="", authors=[], categories=[]), relevance=2)
        self.assertEqual(store.history["2610.12345"]["authors"], ["A. Test"])

    def test_csv_quoted_doi_and_partial_dimensions(self):
        rows = parse_feedback_csv('item,relevance,usefulness,t\n"doi:10.1/a,b",2,,2026-10-26T00:00:00Z\n')
        store = FeedbackStore()
        merge_csv(store, rows)
        self.assertEqual(store.votes["doi:10.1/a,b"].relevance, 2)

    def test_older_csv_cannot_overwrite_new_local_vote(self):
        store = FeedbackStore()
        record_vote(store, paper(), relevance=2)
        merge_csv(store, [(paper().id, 0, None, "2020-01-01T00:00:00Z")])
        self.assertEqual(store.votes["2610.12345"].relevance, 2)

    def test_timestamp_order_is_per_dimension_and_timezone_normalized(self):
        from arxiv_digest.feedback import store_from_dict
        store = store_from_dict({"votes": {paper().id: {"relevance": 0, "usefulness": 2,
            "updated_at": "2026-10-26T00:00:00Z", "relevance_at": "2024-01-01T00:00:00Z",
            "usefulness_at": "2026-10-26T00:00:00Z"}}})
        merge_csv(store, [(paper().id, 2, None, "2025-01-01T08:00:00+08:00")])
        self.assertEqual(store.votes["2610.12345"].relevance, 2)
        self.assertEqual(store.votes["2610.12345"].usefulness, 2)

    def test_corrupt_feedback_metadata_and_float_votes_do_not_crash(self):
        from arxiv_digest.feedback import store_from_dict
        store = store_from_dict({"votes": {"bad": {"rel": 1.8}}, "history": {"bad": {"authors": 12}}})
        self.assertFalse(store.votes)
        self.assertFalse(store.history["bad"]["authors"])


class EvidenceTests(WorkspaceCase):
    def test_missing_and_fabricated_quotes_rejected(self):
        client = FakeClient([{"data": {"value": "invented"}, "result": {"value": "999", "quote": "999 objects"},
                             "numbers": [{"label": "count", "value": "999", "quote": "999 objects"}]}])
        bundle = extract_bundle(client, "fake", paper())
        self.assertTrue(bundle.facts["data"].is_missing)
        self.assertTrue(bundle.facts["result"].is_missing)
        self.assertEqual(bundle.numbers, ())

    def test_basis_cannot_be_changed_by_model(self):
        self.assertEqual(coerce_analysis({"basis": "fulltext"}, profile=Profile(), basis="abstract").basis, "abstract")

    def test_extract_cache_avoids_repeat_model_call(self):
        client = FakeClient([{"result": {"value": "42 clusters", "quote": "We measure 42 clusters."}}])
        first = extract_bundle(client, "fake", paper(), cache_dir=str(self.root))
        second = extract_bundle(client, "fake", paper(), cache_dir=str(self.root))
        self.assertEqual(first, second)
        self.assertEqual(len(client.calls), 1)

    def test_extract_cache_key_includes_call_options(self):
        response = {"result": {"value": "42 clusters", "quote": "We measure 42 clusters."}}
        client = FakeClient([response, response])
        extract_bundle(client, "fake", paper(), cache_dir=str(self.root), options=LLMOptions(thinking=False))
        extract_bundle(client, "fake", paper(), cache_dir=str(self.root), options=LLMOptions(thinking=True))
        self.assertEqual(len(client.calls), 2, "thinking 不同不得命中同一份缓存")

    def test_cached_quotes_revalidated_against_source(self):
        client = FakeClient([{"result": {"value": "42", "quote": "Fabricated quote"}}] * 2)
        for _ in range(2):
            self.assertTrue(extract_bundle(client, "fake", paper(), cache_dir=str(self.root)).facts["result"].is_missing)
        self.assertEqual(len(client.calls), 2, "entirely invalid extracts must not be cached")

    def test_real_quote_cannot_support_fabricated_number(self):
        client = FakeClient([{"result": {"value": "999 clusters", "quote": "We measure 42 clusters."},
                             "numbers": [{"label": "count", "value": "999", "quote": "We measure 42 clusters."}]}])
        bundle = extract_bundle(client, "fake", paper())
        self.assertTrue(bundle.facts["result"].is_missing)
        self.assertFalse(bundle.numbers)

    def test_chinese_numeric_units_and_ranges(self):
        from arxiv_digest.analysis import numbers_supported
        self.assertTrue(numbers_supported("12万星系，0.05-0.3", "120,000 galaxies at 0.05 < z < 0.3"))
        self.assertFalse(numbers_supported("99万", "120,000 galaxies"))

    def test_assessment_cannot_fill_missing_extracted_fact(self):
        client = FakeClient([{}, {"result": "999 invented clusters", "basis": "fulltext", "evidence": ["not in source"]}])
        result = analyze_paper(client, paper(), Profile(), [], extract_model="fake", assess_model="fake")
        self.assertEqual(result.analysis.result, "摘要未提")
        self.assertEqual(result.analysis.basis, "abstract")
        self.assertFalse(result.analysis.evidence)

    def test_unsupported_pulse_headline_not_retained(self):
        material = Material("real", "paper", "Real", "arXiv", "2026-10-20")
        result = coerce_pulse({"headline": "Unsupported", "headline_evidence": ["ghost"]}, [material], [])
        self.assertNotEqual(result.headline, "Unsupported")

    def test_duplicate_topic_labels_merged(self):
        materials = [Material("p", "paper", "Weak lensing", "arXiv", "2026-10-20")]
        hits = count_topics(materials, [("Lensing", ["weak lensing"]), ("Lensing", ["weak lensing"])])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].hits, 2)

    def test_formula_grouping_preserved(self):
        self.assertEqual(to_unicode(r'$\frac{a+b}{c+d}$'), '(a+b)/(c+d)')
        self.assertEqual(to_unicode(r'$\sqrt{x+y}$'), '√(x+y)')
        self.assertEqual(to_unicode(r'$\frac{a}{b}$'), 'a/b')

    def test_fulltext_excerpt_keeps_method_sections_beyond_the_head(self):
        filler = "Intro paragraph. " * 4000           # ~68k 字符，方法节被推到截断点之后
        text = filler + "The covariance matrix comes from mock catalogs. " + filler
        old = text[:36_000]
        new = _excerpt(text, 36_000)
        self.assertNotIn("covariance", old)
        self.assertIn("covariance", new)
        self.assertLessEqual(len(new), 36_000)

    def test_missing_markers_are_not_written_back_into_prose(self):
        self.assertEqual(_strip_missing_notes("样本量 245,236 个星系。全文节选未检出。"),
                         "样本量 245,236 个星系。")
        self.assertEqual(_strip_missing_notes("数据来自 MaNGA，全文节选未检出，样本 1787 个。"),
                         "数据来自 MaNGA，样本 1787 个。")
        self.assertEqual(_strip_missing_notes("摘要未提"), "")


class IntegrationBoundaryTests(WorkspaceCase):
    def test_duplicate_keywords_do_not_double_count_or_repeat(self):
        with patch.dict(os.environ, {"ARXIV_KEYWORDS": "galaxy evolution, galaxy, Galaxy Evolution , "}, clear=False):
            config = load_config()
        self.assertEqual(config.arxiv_keywords, ["galaxy evolution", "galaxy"])

    def test_flag_comments_and_auto_preserve_defaults(self):
        from arxiv_digest.config import _env_bool, _env_tristate
        with patch.dict(os.environ, {"LLM_THINKING": " # use model default", "ANALYSIS_ENABLED": "true # enable"}):
            self.assertIsNone(_env_tristate("LLM_THINKING"))
            self.assertTrue(_env_bool("ANALYSIS_ENABLED", False))
        with patch.dict(os.environ, {"LLM_THINKING": "auto"}):
            self.assertFalse(_env_tristate("LLM_THINKING", False))
        with patch.dict(os.environ, {"LLM_THINKING": "legacy-unknown-value"}):
            self.assertFalse(_env_tristate("LLM_THINKING"))
    def test_delivery_lock_blocks_another_process_and_releases(self):
        from arxiv_digest.storage import delivery_lock
        command = [sys.executable, "-B", "-c", "from arxiv_digest.storage import delivery_lock; import sys; "
                   "ctx=delivery_lock(sys.argv[1]); ctx.__enter__(); ctx.__exit__(None,None,None)", self.config.state_path]
        with delivery_lock(self.config.state_path):
            blocked = subprocess.run(command, capture_output=True)
        released = subprocess.run(command, capture_output=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertEqual(released.returncode, 0)
    def test_ssl_and_starttls_verify_peer_and_use_timeout(self):
        for port in (465, 587):
            transport = "SMTP_SSL" if port == 465 else "SMTP"
            with patch("arxiv_digest.emailer.smtplib." + transport) as smtp:
                send_email("smtp.invalid", port, "u", "p", "a@invalid", "b@invalid", "s", "html", timeout=17)
                kwargs = smtp.call_args.kwargs if port == 465 else smtp.return_value.__enter__.return_value.starttls.call_args.kwargs
                self.assertEqual(kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(kwargs["context"].check_hostname)
                self.assertEqual(smtp.call_args.kwargs["timeout"], 17)

    def test_provider_parameters_are_isolated(self):
        client = FakeClient([{}])
        call_json(client, "gpt-4o-mini", "JSON", options=LLMOptions(thinking=False))
        self.assertNotIn("extra_body", client.calls[0])
        client = FakeClient([{}], "https://api.deepseek.com")
        call_json(client, "deepseek-flash", "JSON", options=LLMOptions(thinking=False))
        self.assertIn("thinking", client.calls[0]["extra_body"])

    def test_overview_sees_all_selected_papers(self):
        from arxiv_digest.llm import generate_digest_content
        client = FakeClient([{"paper_summaries": {str(i): "summary" for i in range(5)}},
                             {"paper_summaries": {"5": "summary"}}, {"overall_summary": "All six"}])
        with patch("openai.OpenAI", return_value=client):
            summary, enriched = generate_digest_content("dummy", "fake", [paper(str(i), title=f"Title {i}") for i in range(6)],
                                                       max_llm_papers=6, batch_size=5)
        self.assertEqual(summary, "All six")
        self.assertIn("Title 5", client.calls[-1]["messages"][-1]["content"])
        self.assertEqual(len(enriched), 6)

    def test_journal_limit_after_topic_gate(self):
        items = [{"DOI": f"10.1234/{i}", "title": ["Planet" if i < 6 else "Weak lensing clusters"],
                  "published": {"date-parts": [[2026, 10, 25]]}} for i in range(7)]
        with patch("arxiv_digest.journals._fetch_crossref", return_value={"message": {"items": items}}):
            found = fetch_journal_papers([Journal("J", "J", ("0000-0000",))], max_per_journal=1,
                                         predicate=lambda p: "lensing" in p.title)
        self.assertEqual(len(found), 1)
        self.assertIn("lensing", found[0].title)

    def test_doi_dedup_handles_changed_title(self):
        from arxiv_digest.journals import merge_with_arxiv
        original = paper(doi="10.1234/x")
        published = paper("doi:10.1234/x", title="Updated publication title", doi="10.1234/x", venue="ApJ")
        merged = merge_with_arxiv([original], [published])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].id, original.id)
        self.assertEqual(merged[0].venue, "ApJ")

    def test_pdf_parser_extracts_text_in_worker_process(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        from arxiv_digest.fulltext import extract_pdf_text
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 100 Td (42 clusters) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        data = io.BytesIO()
        writer.write(data)
        self.assertIn("42 clusters", extract_pdf_text(data.getvalue(), timeout=10))

    def test_stuck_pdf_worker_is_terminated(self):
        from arxiv_digest.fulltext import extract_pdf_text
        context = MagicMock()
        receiver, sender = MagicMock(), MagicMock()
        context.Pipe.return_value = receiver, sender
        receiver.poll.return_value = False
        with patch("arxiv_digest.fulltext.multiprocessing.get_context", return_value=context):
            self.assertEqual(extract_pdf_text(b"%PDF", timeout=1), "")
        context.Process.return_value.terminate.assert_called_once()

    def test_pdf_download_size_bounded(self):
        from arxiv_digest.fulltext import download_pdf
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"%PDF" + b"x" * 8
        with patch("arxiv_digest.fulltext.MAX_PDF_BYTES", 8), patch("arxiv_digest.fulltext.urllib.request.urlopen", return_value=response):
            self.assertIsNone(download_pdf("https://example.invalid/a"))
        response.__enter__.return_value.read.assert_called_once_with(9)

    def test_disabled_source_not_reenabled_by_pack(self):
        from arxiv_digest.sources import _with_pack_sources
        pack = Pack("p", sources_secondary=("https://example.invalid/rss",))
        config = {"feeds": [], "indico": [], "_disabled": [{"url": "https://example.invalid/rss"}]}
        self.assertFalse(_with_pack_sources(config, [pack])["feeds"])

    def test_feedback_html_login_page_not_treated_as_empty_csv(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'<html>Permission required</html>'
        with patch.object(pipeline.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(ValueError):
                pipeline._fetch_sheet_csv("https://example.invalid/private", 10)

    def test_invalid_source_does_not_block_next_source(self):
        from arxiv_digest.sources.rss import fetch_feeds
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"<rss><channel><item><title>T</title></item></channel></rss>"
        with patch("arxiv_digest.sources.rss.urllib.request.urlopen", return_value=response):
            results = fetch_feeds([{"id": "bad", "url": "not a URL"}, {"id": "good", "url": "https://example.invalid/rss"}])
        self.assertFalse(results[0].ok)
        self.assertTrue(results[1].ok)

    def test_replay_does_not_fetch_even_when_legacy_flag_is_true(self):
        from scripts.build_preview import build_papers
        with patch("scripts.build_preview.fetch_fulltext") as fetch:
            build_papers({"papers": [{**paper_to_dict(paper()), "fetch_fulltext": True}]})
        fetch.assert_not_called()

    def test_radar_identity_can_be_marked_seen(self):
        radar = select_blocks([Neighborhood("co", "Cosmology", ("astro-ph.CO",))], [paper()])
        state = State()
        mark_seen(state, radar.hits, day="2026-10-26")
        self.assertIn("2610.12345", state.seen)

    def test_new_pack_can_define_journal_gate_without_python_changes(self):
        pack = Pack("ml", categories=("cs.LG",), journal_terms=("transformer",), title_keywords=("transformer",))
        with patch.object(pipeline, "load_packs", return_value=(pack,)):
            terms = pipeline._journal_topic_terms(replace(self.config, fields=["ml"]))
        self.assertEqual(terms, ("transformer",))
        self.assertEqual(dispatch([pack], [], title="A transformer model").id, "ml")

    def test_generic_pack_available_in_real_assessment_selection(self):
        selected = pipeline._assessment_packs(replace(self.config, fields=["galaxy_cosmology"]))
        self.assertTrue(any(pack.is_generic for pack in selected))
        self.assertTrue(dispatch(selected, ["hep-th"]).is_generic)

    def test_feedback_get_rejects_dns_rebinding_host(self):
        from scripts.feedback_server import _Handler
        handler = object.__new__(_Handler)
        handler.server = SimpleNamespace(signing_key="", nonces={})
        handler.path = "/log?item=p&rel=2"
        handler.headers = {"Host": "attacker.invalid"}
        handler._send = lambda status, body: setattr(handler, "status", status)
        handler.do_GET()
        self.assertEqual(handler.status, 403)

    def test_primary_category_and_legacy_arxiv_ids(self):
        from arxiv_digest.arxiv_client import _entry_to_paper
        raw = '<entry xmlns="http://www.w3.org/2005/Atom" xmlns:a="http://arxiv.org/schemas/atom"><id>http://arxiv.org/abs/astro-ph/0601234v1</id><title>T</title><summary>S</summary><published>2006-01-01T00:00:00Z</published><updated>2006-01-01T00:00:00Z</updated><category term="astro-ph.GA"/><category term="astro-ph.CO"/><a:primary_category term="astro-ph.CO"/></entry>'
        result = _entry_to_paper(fromstring(raw))
        self.assertEqual(result.id, "astro-ph/0601234v1")
        self.assertEqual(result.categories[0], "astro-ph.CO")
        self.assertTrue(result.abs_url.startswith("https://"))

    def test_server_side_date_filter(self):
        from arxiv_digest import arxiv_client
        with patch.object(arxiv_client, "_fetch_url", return_value='<feed xmlns="http://www.w3.org/2005/Atom"/>') as fetch:
            arxiv_client.fetch_recent_papers(["astro-ph.CO"], 1)
        self.assertIn("submittedDate", fetch.call_args.args[0])

    def test_feedback_signature_tampering_and_expiry(self):
        signed = signed_params({"item": "p", "rel": 2}, "test-key")
        self.assertTrue(verify_params(signed, "test-key"))
        self.assertFalse(verify_params({**signed, "rel": "0"}, "test-key"))
        self.assertFalse(verify_params({**signed, "expires": "0"}, "test-key"))

    def test_unsafe_links_not_rendered(self):
        self.assertEqual(_safe_url("javascript:alert(1)"), "")
        self.assertEqual(_safe_url("https://example.invalid"), "https://example.invalid")

    def test_default_digest_path_supports_feedback_metadata(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_config().digest_json_path, "digest.json",
                             "默认要落 digest.json，否则反馈 CLI/服务学不到作者与分类")

    def test_merge_state_falls_back_when_report_missing(self):
        from scripts.merge_state import main as merge_main
        remote = self.root / "remote.json"
        local = self.root / "local.json"
        remote.write_text(json.dumps({"version": 2, "seen": {"2610.00001": "2026-10-20"}}), encoding="utf-8")
        local.write_text(json.dumps({"version": 2, "seen": {"2610.00002": "2026-10-26"}}), encoding="utf-8")
        for index, report in enumerate((self.root / "missing.json", self.root / "broken.json")):
            if report.name == "broken.json":
                report.write_text("{not json", encoding="utf-8")
            output = self.root / f"merged-{index}.json"
            code = merge_main([str(remote), str(local), str(output), "--report", str(report),
                               "--today", "2026-10-26", "--timezone", "Asia/Shanghai"])
            self.assertEqual(code, 0)
            merged = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(set(merged["seen"]), {"2610.00001", "2610.00002"})

    def test_package_version_matches_pyproject(self):
        import arxiv_digest
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        self.assertIn(f'version = "{arxiv_digest.__version__}"', pyproject.read_text(encoding="utf-8"))

    def test_get_feedback_is_read_only_and_post_requires_challenge(self):
        from scripts.feedback_server import _Handler
        server = SimpleNamespace(signing_key="", nonces={}, state_path=Path(self.config.state_path),
                                 feedback_path=Path(self.config.feedback_path), digest_path=Path(self.config.digest_json_path))
        handler = object.__new__(_Handler)
        handler.server = server
        handler.path = "/log?item=2610.12345v1&rel=2"
        handler.headers = {"Host": "127.0.0.1:8765"}
        handler._send = lambda status, body: setattr(handler, "status", status)
        handler.do_GET()
        self.assertEqual(handler.status, 200)
        self.assertFalse(Path(self.config.feedback_path).exists())
        nonce = next(iter(server.nonces))
        data = ("nonce=" + nonce).encode()
        handler.path = "/log"
        handler.headers["Content-Length"] = str(len(data))
        handler.rfile = io.BytesIO(data)
        handler.do_POST()
        self.assertEqual(handler.status, 200)
        self.assertEqual(load_feedback(self.config.feedback_path).votes["2610.12345"].relevance, 2)
        handler.rfile = io.BytesIO(data)
        handler.do_POST()
        self.assertEqual(handler.status, 403)


if __name__ == "__main__":
    unittest.main()
