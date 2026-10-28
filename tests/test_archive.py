from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import tests  # noqa: F401 - 让测试在 discover 时也装上网络守卫

from arxiv_digest import archive, pipeline
from arxiv_digest.archive import (
    DICT_HEADERS,
    OVERVIEW_HEADERS,
    PAPERS_HEADERS,
    TOPICS_HEADERS,
    ArchiveError,
    HttpTransport,
    WeekWindow,
    build_rows,
    summarize_week,
    week_window,
)
from arxiv_digest.calendar import empty_bundle
from arxiv_digest.config import load_config
from arxiv_digest.models import Analysis, Concept, KeyNumber, Paper, RedFlag, ScoreBreakdown
from arxiv_digest.packs import load_packs, select_packs

PACKS = select_packs(load_packs("config/packs"), ())


def make_paper(identifier="2610.12345v1", title="Weak lensing mass calibration for galaxy clusters",
               summary="We calibrate the richness-mass relation with weak lensing in DESI clusters.",
               day=26, **changes):
    stamp = datetime(2026, 10, day, 3, tzinfo=timezone.utc)
    base = Paper(identifier, title, ["Alice Astronomer", "Bob Builder"], summary, stamp, stamp,
                 ["astro-ph.CO"], "https://arxiv.org/pdf/2610.12345", "https://arxiv.org/abs/2610.12345")
    return replace(base, **changes)


def make_analysis():
    return Analysis(
        relevance=0.91,
        relevance_kind="direct",
        verdict="用弱透镜标定 richness-mass 关系，可直接引用质量标定误差。",
        method="分箱测量 ΔΣ 并用 NFW 拟合质量。",
        result="richness 与质量偏差约 6%。",
        background="星系团计数是宇宙学探针，质量标定是主要系统误差。",
        method_detail="按 richness 分箱，拟合弱透镜剖面，比较测得质量与真实质量。",
        concepts=(Concept("richness", "富度"), Concept("weak lensing", "弱透镜")),
        numbers=(KeyNumber("样本", "245236", "245,236 galaxies"),),
        red_flags=(RedFlag("selection", "missing", "未讨论选择函数"),),
        basis="fulltext",
        confidence="medium",
    )


def row(identifier, week="2026-10-25", **values):
    base = {
        "week_start": week,
        "week_label": "2026-10-25 至 2026-10-31",
        "date": "2026-10-26",
        "id": identifier,
        "doi": "",
        "venue": "",
        "source_kind": "arxiv",
        "pack": "galaxy_cosmology",
        "categories": "astro-ph.CO",
        "topics": "",
        "keywords": "",
        "title": "t",
        "title_zh": "",
        "first_author": "A",
        "n_authors": "1",
        "summary_snippet": "s",
        "score": "5.00",
        "rating": "4",
        "s_keyword": "+5.00",
        "s_zotero": "+0.00",
        "s_category": "+0.20",
        "s_library": "+0.00",
        "s_feedback": "+0.00",
        "shown": "1",
        "deep": "0",
        "relevance": "",
        "relevance_kind": "",
        "verdict": "",
        "concepts": "",
        "n_numbers": "",
        "n_red_flags": "",
        "basis": "",
        "confidence": "",
    }
    base.update(values)
    return base


class WeekTests(unittest.TestCase):
    def test_week_starts_on_sunday(self):
        week = week_window(date(2026, 10, 28))  # 周三
        self.assertEqual(week.start, date(2026, 10, 25))
        self.assertEqual(week.end, date(2026, 11, 1))
        self.assertEqual(week.label, "2026-10-25 至 2026-10-31")

    def test_sunday_belongs_to_the_next_week(self):
        week = week_window(date(2026, 11, 1))  # 周日
        self.assertEqual(week.start, date(2026, 11, 1))
        self.assertEqual(week.end, date(2026, 11, 8))


class RowTests(unittest.TestCase):
    def test_build_rows_shape_and_flags(self):
        deep_paper = make_paper(identifier="2610.12345v1",
                                breakdown=ScoreBreakdown(keyword=2.4, category=0.2))
        brief_paper = make_paper(identifier="2610.22222v1", title="Galaxy quenching in clusters",
                                 summary="We study quenching with mock catalogs.", day=27)
        journal_paper = make_paper(identifier="doi:10.3847/1538-4357/xyz", title="Mass calibration in ApJ",
                                   venue="ApJ", day=25)

        rows = build_rows(
            papers=[deep_paper, brief_paper, journal_paper],
            ranked=[deep_paper, brief_paper],
            enriched=[replace(deep_paper, analysis=make_analysis())],
            packs=PACKS,
            keywords=["weak lensing", "quenching"],
            timezone_name="Asia/Shanghai",
        )

        self.assertEqual(len(rows), 3)
        for values in rows:
            self.assertEqual(len(values), len(PAPERS_HEADERS))

        deep = dict(zip(PAPERS_HEADERS, rows[0]))
        self.assertIn("弱透镜", deep["topics"])
        self.assertIn("weak lensing", deep["keywords"])
        self.assertEqual(deep["deep"], "1")
        self.assertEqual(deep["shown"], "1")
        self.assertEqual(deep["s_keyword"], "+2.40")
        self.assertEqual(deep["verdict"][:2], "用弱")
        self.assertIn("weak lensing", deep["concepts"])
        self.assertEqual(deep["n_numbers"], "1")
        self.assertEqual(deep["n_red_flags"], "1")
        self.assertEqual(deep["basis"], "fulltext")

        journal = dict(zip(PAPERS_HEADERS, rows[2]))
        self.assertEqual(journal["source_kind"], "journal")
        self.assertEqual(journal["venue"], "ApJ")
        self.assertEqual(journal["shown"], "0")
        self.assertEqual(journal["deep"], "0")

    def test_cells_are_single_line_and_formula_safe(self):
        paper = make_paper(title="=HYPERLINK(\"http://evil\")", summary="line1\nline2\tline3")
        values = dict(zip(PAPERS_HEADERS, build_rows(
            papers=[paper], ranked=[], enriched=[], packs=PACKS, keywords=[])[0]))
        self.assertTrue(values["title"].startswith("'="))
        self.assertNotIn("\n", values["summary_snippet"])
        self.assertNotIn("\t", values["summary_snippet"])

    def test_topics_use_word_boundaries(self):
        paper = make_paper(title="Massive halos in massive simulations",
                           summary="A massive dataset about galaxy clusters.")
        values = dict(zip(PAPERS_HEADERS, build_rows(
            papers=[paper], ranked=[], enriched=[], packs=PACKS, keywords=["mass"])[0]))
        self.assertIn("星系团 / 群", values["topics"])
        # 关键词 "mass" 不应命中 "massive"
        self.assertEqual(values["keywords"], "")


class SummaryTests(unittest.TestCase):
    def summary(self, *, rows, prev_rows=(), history=(), finalized=False):
        return summarize_week(
            week=WeekWindow(date(2026, 10, 25), date(2026, 11, 1)),
            rows=rows, prev_rows=prev_rows, history_topics=history,
            finalized=finalized, topic_packs={"弱透镜": "galaxy_cosmology"},
            topic_aliases={"弱透镜": ("weak lensing", "cosmic shear")},
        )

    def test_share_delta_and_ma4(self):
        current = [row(f"p{index}", topics="弱透镜" if index < 2 else "") for index in range(4)]
        previous = [row(f"q{index}", week="2026-10-18", topics="弱透镜" if index < 2 else "")
                    for index in range(3)]
        history = [
            {"week_start": "2026-10-18", "topic": "弱透镜", "papers": "2", "first_week": "2026-10-04"},
            {"week_start": "2026-10-11", "topic": "弱透镜", "papers": "0", "first_week": "2026-10-04"},
            {"week_start": "2026-10-04", "topic": "弱透镜", "papers": "2", "first_week": "2026-10-04"},
        ]
        topic_rows, dict_rows, overview = self.summary(rows=current, prev_rows=previous, history=history)
        values = dict(zip(TOPICS_HEADERS, topic_rows[0]))
        self.assertEqual(values["topic"], "弱透镜")
        self.assertEqual(values["papers"], "2")
        self.assertEqual(values["share"], "0.5000")
        self.assertEqual(values["prev_papers"], "2")
        self.assertEqual(values["delta_pct"], "+0.0")
        # (当前 2 + 上周 2 + 前两周 0 + 前三周 2) / 4 = 1.5
        self.assertEqual(values["ma4"], "1.50")
        self.assertEqual(values["arxiv_papers"], "2")
        self.assertEqual(values["journal_papers"], "0")
        self.assertEqual(values["first_week"], "2026-10-04")

        dictionary = dict(zip(DICT_HEADERS, dict_rows[0]))
        self.assertEqual(dictionary["topic"], "弱透镜")
        self.assertIn("weak lensing", dictionary["aliases"])
        self.assertEqual(dictionary["pack"], "galaxy_cosmology")
        self.assertEqual(dictionary["source"], "pack-topic")
        self.assertEqual(dictionary["total_papers"], "6")

        overview_values = dict(zip(OVERVIEW_HEADERS, overview))
        self.assertEqual(overview_values["sampled"], "4")
        self.assertEqual(overview_values["arxiv_papers"], "4")
        self.assertEqual(overview_values["top5_topics"], "弱透镜=2")

    def test_delta_is_blank_when_previous_week_is_empty(self):
        current = [row("p1", topics="弱透镜")]
        topic_rows, _, _ = self.summary(rows=current)
        values = dict(zip(TOPICS_HEADERS, topic_rows[0]))
        self.assertEqual(values["prev_papers"], "0")
        self.assertEqual(values["delta_pct"], "")
        # 没有历史周时 ma4 只按当周平均
        self.assertEqual(values["ma4"], "1.00")

    def test_finalized_and_rising(self):
        current = [row(f"p{index}", topics="弱透镜" if index < 4 else "") for index in range(4)]
        previous = [row(f"q{index}", week="2026-10-18", topics="弱透镜" if index < 2 else "")
                    for index in range(4)]
        _, _, overview = self.summary(rows=current, prev_rows=previous, finalized=True)
        values = dict(zip(OVERVIEW_HEADERS, overview))
        self.assertEqual(values["finalized"], "1")
        self.assertEqual(values["rising"], "弱透镜=+100%")
        self.assertEqual(values["falling"], "")

    def test_falls_back_when_week_has_no_papers(self):
        topic_rows, dict_rows, overview = self.summary(rows=[])
        values = dict(zip(OVERVIEW_HEADERS, overview))
        self.assertEqual(values["sampled"], "0")
        self.assertEqual(topic_rows, [])
        self.assertEqual(dict_rows, [])


class FakeTransport:
    def __init__(self):
        self.tables: dict[str, tuple[list[str], list[list[str]]]] = {}
        self.upserts: list[tuple[str, int]] = []
        self.reads: list[tuple[str, dict]] = []

    def read(self, sheet, where=None):
        headers, rows = self.tables.get(sheet, ([], []))
        self.reads.append((sheet, dict(where or {})))
        result = []
        for values in rows:
            item = dict(zip(headers, values))
            if where and any(str(item.get(column, "")) != str(value) for column, value in where.items()):
                continue
            result.append(item)
        return result

    def upsert(self, sheet, headers, rows, key):
        self.upserts.append((sheet, len(rows)))
        stored_headers, stored = self.tables.setdefault(sheet, (list(headers), []))
        positions = {}
        for index, values in enumerate(stored):
            item = dict(zip(stored_headers, values))
            positions[tuple(str(item.get(column, "")) for column in key)] = index
        for values in rows:
            item = dict(zip(headers, values))
            pair = tuple(str(item.get(column, "")) for column in key)
            if pair in positions:
                stored[positions[pair]] = list(values)
            else:
                positions[pair] = len(stored)
                stored.append(list(values))
        return len(rows)

    def health(self):
        return {"ok": True}


def archive_config():
    return SimpleNamespace(
        archive_webhook_url="https://example.invalid/exec",
        archive_token="token",
        archive_timeout=20,
        archive_max_rows=400,
        archive_snippet_chars=160,
        digest_timezone="Asia/Shanghai",
        packs_dir="config/packs",
        fields=(),
    )


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.config = archive_config()
        paper = make_paper()
        self.rows = build_rows(papers=[paper, make_paper(identifier="2610.99999v1", title="BAO measurement",
                                                          summary="Baryon acoustic oscillations from DESI.", day=27)],
                               ranked=[paper], enriched=[], packs=PACKS, keywords=["weak lensing"],
                               timezone_name="Asia/Shanghai")

    def test_record_upserts_and_is_idempotent(self):
        first = archive.record(self.config, self.rows, today=date(2026, 10, 28), transport=self.transport)
        self.assertTrue(first["ok"])
        self.assertEqual(first["written"], 2)
        self.assertEqual(first["weeks"][0]["week_start"], "2026-10-25")
        self.assertFalse(first["weeks"][0]["finalized"])

        headers, stored = self.transport.tables["papers"]
        self.assertEqual(headers, list(PAPERS_HEADERS))
        self.assertEqual(len(stored), 2)
        self.assertTrue(self.transport.tables["weekly_topics"][1])
        self.assertEqual(len(self.transport.tables["weekly_overview"][1]), 1)
        self.assertTrue(self.transport.tables["dict"][1])

        second = archive.record(self.config, self.rows, today=date(2026, 10, 28), transport=self.transport)
        self.assertEqual(second["written"], 0)
        self.assertEqual(len(self.transport.tables["papers"][1]), 2)

    def test_record_reads_previous_week_for_delta(self):
        previous = row("old-1", week="2026-10-18", topics="弱透镜")
        self.transport.tables["papers"] = (list(PAPERS_HEADERS), [
            [previous[column] for column in PAPERS_HEADERS]])
        archive.record(self.config, self.rows, today=date(2026, 10, 28), transport=self.transport)
        topics = [dict(zip(TOPICS_HEADERS, values)) for values in self.transport.tables["weekly_topics"][1]]
        weak = [item for item in topics if item["topic"] == "弱透镜"][0]
        self.assertEqual(weak["prev_papers"], "1")

    def test_record_skips_empty_rows(self):
        result = archive.record(self.config, [], today=date(2026, 10, 28), transport=self.transport)
        self.assertEqual(result["written"], 0)
        self.assertEqual(self.transport.upserts, [])

    def test_record_writes_feedback_view_when_votes_given(self):
        votes = {"2610.12345": (2, 1)}
        result = archive.record(self.config, self.rows, today=date(2026, 10, 28),
                                transport=self.transport, votes=votes)
        headers, stored = self.transport.tables["feedback_view"]
        self.assertEqual(headers, list(archive.FEEDBACK_HEADERS))
        self.assertEqual(result["weeks"][0]["feedback"], len(stored))
        self.assertGreaterEqual(len(stored), 1)
        self.assertEqual({dict(zip(headers, values))["mean_relevance"] for values in stored}, {"2.0"})

    def test_record_skips_feedback_view_without_votes(self):
        archive.record(self.config, self.rows, today=date(2026, 10, 28), transport=self.transport)
        self.assertNotIn("feedback_view", self.transport.tables)

    def test_transport_errors_surface(self):
        broken = FakeTransport()
        with patch.object(broken, "read", side_effect=ArchiveError("boom")):
            with self.assertRaises(ArchiveError):
                archive.record(self.config, self.rows, transport=broken)


class FeedbackViewTests(unittest.TestCase):
    def test_votes_are_aggregated_by_topic(self):
        week = WeekWindow(date(2026, 10, 25), date(2026, 11, 1))
        rows = [
            row("2610.11111v1", topics="弱透镜", source_kind="arxiv"),
            row("2610.22222v1", topics="弱透镜;星系团 / 群", source_kind="arxiv"),
            row("doi:10.1/x", topics="星系团 / 群", source_kind="journal", venue="ApJ"),
        ]
        votes = {"2610.11111": (2, 2), "2610.22222": (1, 0), "doi:10.1/x": (0, None)}
        values = {item[2]: dict(zip(archive.FEEDBACK_HEADERS, item))
                  for item in archive.build_feedback_rows(rows=rows, votes=votes, week=week)}
        weak = values["弱透镜"]
        self.assertEqual(weak["votes"], "2")
        self.assertEqual(weak["mean_relevance"], "1.5")
        self.assertEqual(weak["mean_usefulness"], "1.0")
        self.assertEqual(weak["top_useful"], "2610.11111v1")
        self.assertEqual(weak["week_start"], "2026-10-25")
        cluster = values["星系团 / 群"]
        self.assertEqual(cluster["votes"], "2")
        self.assertEqual(cluster["mean_relevance"], "0.5")
        self.assertEqual(cluster["mean_usefulness"], "0.0")

    def test_version_suffix_and_doi_case_are_normalized(self):
        week = WeekWindow(date(2026, 10, 25), date(2026, 11, 1))
        rows = [row("2610.99999v3", topics="弱透镜"), row("doi:10.3847/ABC", topics="弱透镜", source_kind="journal")]
        votes = {"2610.99999": (2, None), "doi:10.3847/abc": (None, 2)}
        values = dict(zip(archive.FEEDBACK_HEADERS,
                          archive.build_feedback_rows(rows=rows, votes=votes, week=week)[0]))
        self.assertEqual(values["votes"], "2")
        self.assertEqual(values["mean_relevance"], "2.0")
        self.assertEqual(values["mean_usefulness"], "2.0")

    def test_no_votes_means_no_rows(self):
        week = WeekWindow(date(2026, 10, 25), date(2026, 11, 1))
        self.assertEqual(archive.build_feedback_rows(rows=[row("2610.1v1", topics="弱透镜")],
                                                     votes={}, week=week), [])


class SignatureTests(unittest.TestCase):
    def test_signature_is_deterministic_and_key_dependent(self):
        transport = HttpTransport("https://example.invalid", "secret")
        self.assertEqual(transport._sign(b"abc"), transport._sign(b"abc"))
        self.assertNotEqual(transport._sign(b"abc"), HttpTransport("https://x", "other")._sign(b"abc"))
        self.assertEqual(len(transport._sign(b"abc")), 64)


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = load_config()
        self.assertTrue(config.archive_enabled)
        self.assertEqual(config.archive_timeout, 20)
        self.assertEqual(config.archive_max_rows, 400)
        self.assertEqual(config.archive_snippet_chars, 160)

    def test_validation_rejects_bad_rows(self):
        config = load_config()
        with self.assertRaises(ValueError):
            replace(config, archive_max_rows=0)
        with self.assertRaises(ValueError):
            replace(config, archive_snippet_chars=10)


class DeliveryTests(unittest.TestCase):
    def make_result(self):
        return pipeline.PipelineResult(
            papers=[], ranked_candidates=[], ranked=[], summary="", enriched=[],
            calendar=empty_bundle(), report={"date": "2026-10-28", "edition_key": "", "delivery": "pending"},
            ledger=[["row"]])

    def config(self):
        return SimpleNamespace(digest_timezone="Asia/Shanghai", run_report_path="", digest_json_path="",
                               state_path="state.json", feedback_path="feedback.json")

    def test_commit_writes_ledger(self):
        result = self.make_result()
        with patch.object(pipeline.archive, "record", return_value={"ok": True, "written": 1}) as record, \
                patch.object(pipeline, "update_diagnostics"):
            pipeline.commit_delivery(self.config(), result)
        record.assert_called_once()
        self.assertEqual(result.report["archive"], {"ok": True, "written": 1})
        self.assertEqual(result.report["delivery"], "committed")

    def test_preview_never_writes(self):
        result = self.make_result()
        with patch.object(pipeline.archive, "record") as record, \
                patch.object(pipeline, "update_diagnostics"):
            pipeline.commit_delivery(self.config(), result, delivery="preview")
        record.assert_not_called()

    def test_archive_failure_does_not_break_delivery(self):
        result = self.make_result()
        with patch.object(pipeline.archive, "record", side_effect=ArchiveError("down")), \
                patch.object(pipeline, "update_diagnostics"):
            pipeline.commit_delivery(self.config(), result)
        self.assertFalse(result.report["archive"]["ok"])
        self.assertEqual(result.report["delivery"], "committed")


if __name__ == "__main__":
    unittest.main()
