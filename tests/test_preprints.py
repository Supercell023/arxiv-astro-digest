"""离线测试：期刊 DOI → arXiv 预印本（补摘要 / 取全文），以及数值护栏的分句降级。"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from tests.test_regressions import WorkspaceCase
from arxiv_digest import journals, pipeline, preprints
from arxiv_digest.analysis import _salvage_text
from arxiv_digest.config import load_config
from arxiv_digest.journals import Journal
from arxiv_digest.models import Paper
from arxiv_digest.preprints import PreprintMatch, match_candidates


ATOM_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2604.06318v2</id>
    <published>2026-04-07T17:59:59Z</published>
    <title>Introducing sapphire: Towards hybrid physics-informed, data-driven modeling of galaxy formation</title>
    <summary>We present sapphire, a differentiable semi-analytic model of galaxy formation.</summary>
    <author><name>Viraj Pandya</name></author>
    <author><name>Greg L. Bryan</name></author>
  </entry>
</feed>"""


def journal_paper(**changes) -> Paper:
    values = dict(
        id="doi:10.3847/1538-4357/aea55e",
        title="Introducing sapphire : Towards Hybrid Physics-informed, Data-driven Modeling of Galaxy Formation",
        authors=["Viraj Pandya", "Greg L. Bryan"],
        summary="",
        published=datetime(2026, 10, 20, tzinfo=timezone.utc),
        updated=datetime(2026, 10, 20, tzinfo=timezone.utc),
        categories=[],
        pdf_url="https://doi.org/10.3847/1538-4357/aea55e",
        abs_url="https://doi.org/10.3847/1538-4357/aea55e",
        venue="ApJ",
        doi="10.3847/1538-4357/aea55e",
    )
    values.update(changes)
    return Paper(**values)


MATCH = PreprintMatch(
    arxiv_id="2604.06318",
    title="Introducing sapphire: Towards hybrid physics-informed, data-driven modeling of galaxy formation",
    summary="We present sapphire, a differentiable semi-analytic model of galaxy formation.",
    published=datetime(2026, 4, 7, tzinfo=timezone.utc),
    authors=("Viraj Pandya", "Greg L. Bryan"),
)


class PreprintParsingTests(WorkspaceCase):
    def test_parse_entries_strips_version_and_reads_fields(self):
        parsed = preprints._parse_entries(ATOM_ONE)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].arxiv_id, "2604.06318")
        self.assertIn("sapphire", parsed[0].summary)
        self.assertEqual(parsed[0].authors[0], "Viraj Pandya")
        self.assertEqual(parsed[0].pdf_url, "https://arxiv.org/pdf/2604.06318")

    def test_match_requires_title_author_and_year(self):
        paper = journal_paper()
        self.assertIsNotNone(match_candidates(paper, [MATCH]))
        other_author = replace(MATCH, authors=("Someone Else", "Nobody Here"))
        self.assertIsNone(match_candidates(paper, [other_author]))
        old = replace(MATCH, published=datetime(2019, 4, 7, tzinfo=timezone.utc))
        self.assertIsNone(match_candidates(paper, [old]))
        unrelated = replace(MATCH, title="A completely different paper about exoplanet atmospheres")
        self.assertIsNone(match_candidates(paper, [unrelated]))

    def test_missing_authors_still_matches_on_title(self):
        paper = journal_paper(authors=[])
        candidate = replace(MATCH, authors=())
        self.assertIsNotNone(match_candidates(paper, [candidate]))


class PreprintCacheTests(WorkspaceCase):
    def test_hit_is_cached_and_second_lookup_is_skipped(self):
        cache_dir = self.root / "cache"
        lookup = MagicMock(return_value=MATCH)
        with patch.object(preprints, "find_preprint", lookup):
            first = preprints.resolve_preprint(journal_paper(), cache_dir=cache_dir)
            second = preprints.resolve_preprint(journal_paper(), cache_dir=cache_dir)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(second.arxiv_id, "2604.06318")

    def test_miss_is_cached_for_a_while(self):
        cache_dir = self.root / "cache"
        lookup = MagicMock(return_value=None)
        with patch.object(preprints, "find_preprint", lookup):
            self.assertIsNone(preprints.resolve_preprint(journal_paper(), cache_dir=cache_dir))
            self.assertIsNone(preprints.resolve_preprint(journal_paper(), cache_dir=cache_dir))
        self.assertEqual(lookup.call_count, 1)

    def test_pdf_url_uses_arxiv(self):
        with patch.object(preprints, "find_preprint", MagicMock(return_value=MATCH)):
            url = preprints.preprint_pdf_url(journal_paper(), cache_dir=self.root / "cache")
        self.assertEqual(url, "https://arxiv.org/pdf/2604.06318")

    def test_enrich_summary_keeps_journal_identity(self):
        cache_dir = self.root / "cache"
        with patch.object(preprints, "find_preprint", MagicMock(return_value=MATCH)):
            enriched = preprints.enrich_summary(journal_paper(), cache_dir=cache_dir)
        self.assertIn("sapphire", enriched.summary)
        self.assertEqual(enriched.venue, "ApJ")
        self.assertEqual(enriched.doi, "10.3847/1538-4357/aea55e")


class PreprintPipelineTests(WorkspaceCase):
    def test_fetch_fulltexts_uses_arxiv_pdf_for_doi(self):
        config = replace(
            self.config,
            deep_papers=1,
            preprint_enabled=True,
            cache_dir=str(self.root / "cache"),
        )
        paper = journal_paper()
        with patch.object(pipeline, "preprint_pdf_url", return_value="https://arxiv.org/pdf/2604.06318"), \
             patch.object(pipeline, "fetch_fulltext", return_value="FULLTEXT") as fetch:
            texts = pipeline._fetch_fulltexts(config, [paper])
        self.assertEqual(texts, {paper.id: "FULLTEXT"})
        self.assertTrue(fetch.call_args[0][0].endswith("/pdf/2604.06318"))
        self.assertEqual(paper.venue, "ApJ")

    def test_missing_preprint_falls_back_to_abstract(self):
        config = replace(self.config, deep_papers=1, cache_dir=str(self.root / "cache"))
        with patch.object(pipeline, "preprint_pdf_url", return_value=None), \
             patch.object(pipeline, "fetch_fulltext") as fetch:
            texts = pipeline._fetch_fulltexts(config, [journal_paper()])
        self.assertEqual(texts, {})
        fetch.assert_not_called()

    def test_analysis_salvage_keeps_supported_clause(self):
        source = "结论稳健。样本共 128 个星系。"
        text = "结论稳健。样本共 999 个星系。"
        salvaged = _salvage_text(text, source)
        self.assertIn("结论稳健", salvaged)
        self.assertNotIn("999", salvaged)


class JournalPreprocessTests(WorkspaceCase):
    def test_preprocess_runs_before_predicate(self):
        journal = Journal(id="NatAs", name="Nature Astronomy", issn=("2397-3366",), pack="galaxy_cosmology")
        payload = {
            "message": {
                "items": [
                    {
                        "type": "journal-article",
                        "DOI": "10.1038/s41550-026-00001-1",
                        "title": ["Galaxy clusters at cosmic noon"],
                        "published": {"date-parts": [[2026, 10, 20]]},
                    }
                ]
            }
        }
        seen: list[str] = []

        def preprocess(paper: Paper) -> Paper:
            seen.append(paper.summary)
            return replace(paper, summary="A weak lensing study of galaxy clusters.")

        with patch.object(journals, "_fetch_crossref", return_value=payload):
            papers = journals.fetch_journal_papers(
                [journal],
                lookback_days=2,
                rows=5,
                max_per_journal=5,
                predicate=lambda paper: "lensing" in paper.summary,
                preprocess=preprocess,
            )
        self.assertEqual(seen, [""])  # 进闸门前摘要确实是空的
        self.assertEqual(len(papers), 1)
        self.assertIn("lensing", papers[0].summary)


class PreprintConfigTests(WorkspaceCase):
    def test_defaults_are_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
        self.assertTrue(config.preprint_enabled)
        self.assertEqual(config.preprint_timeout, 20)
        self.assertEqual(config.preprint_max_results, 5)
        self.assertEqual(config.preprint_abstract_lookups, 4)
