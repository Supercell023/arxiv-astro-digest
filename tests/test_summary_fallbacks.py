from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from arxiv_digest import analysis, llm, radar, translate
from arxiv_digest.evidence import numbers_supported, numbers_supported_ignoring_dates
from arxiv_digest.emailer import render_email_html
from arxiv_digest.models import Analysis, Paper
from arxiv_digest.sources import Item

STAMP = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)


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
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)), finish_reason="stop")]
        )


def paper(**updates) -> Paper:
    base = Paper(
        id="2610.12345v1",
        title="Weak lensing mass calibration with 42 clusters",
        authors=["A. Author"],
        summary="We calibrate masses for 42 clusters with weak lensing in 2026.",
        published=STAMP,
        updated=STAMP,
        categories=["astro-ph.CO"],
        pdf_url="",
        abs_url="",
    )
    return replace(base, **updates)


class SummaryKeyTests(unittest.TestCase):
    def test_key_accepts_version_prefix_and_case(self):
        sources = {"2610.12345v1": "summary"}
        self.assertEqual(llm._summary_key("2610.12345v1", sources), "2610.12345v1")
        self.assertEqual(llm._summary_key("2610.12345", sources), "2610.12345v1")
        self.assertEqual(llm._summary_key("arXiv:2610.12345V1", sources), "2610.12345v1")
        self.assertEqual(llm._summary_key("9999.99999v1", sources), "")


class CallBatchTests(unittest.TestCase):
    def test_english_or_fabricated_entries_are_rejected(self):
        first = paper()
        second = paper(id="2610.54321v1", title="Cluster cosmology")
        client = FakeClient([
            {
                "paper_summaries": {
                    first.id: "This paper measures 42 clusters.",  # 没有中文
                    second.id: "该文测得 999 个星系团。",  # 999 原文没有
                }
            }
        ])
        _, summaries = llm._call_batch(client, "fake", [first, second], ask_overall=False, max_abstract_chars=1800)
        self.assertEqual(summaries, {})

    def test_supported_chinese_summary_is_kept(self):
        item = paper()
        client = FakeClient([{"paper_summaries": {item.id: "该文用弱透镜为 42 个星系团标定质量。"}}])
        _, summaries = llm._call_batch(client, "fake", [item], ask_overall=False, max_abstract_chars=1800)
        self.assertIn(item.id, summaries)

    def test_fabricated_clause_is_salvaged(self):
        item = paper()
        client = FakeClient([{"paper_summaries": {item.id: "该文为 42 个星系团标定质量。样本扩展到 999 个。"}}])
        _, summaries = llm._call_batch(client, "fake", [item], ask_overall=False, max_abstract_chars=1800)
        self.assertIn("42 个星系团", summaries.get(item.id, ""))
        self.assertNotIn("999", summaries.get(item.id, ""))


class RetryTests(unittest.TestCase):
    def test_missing_batch_entry_is_retried_individually(self):
        first = paper()
        second = paper(id="2610.54321v1", title="Cluster cosmology")
        client = FakeClient([
            {"paper_summaries": {first.id: "该文为 42 个星系团标定质量。"}},
            {"paper_summaries": {second.id: "本文讨论星系团宇宙学。"}},
            {"overall_summary": "本日两篇。"},
        ])
        with patch("openai.OpenAI", return_value=client):
            summary, papers = llm.generate_digest_content(
                "dummy", "fake", [first, second], max_llm_papers=2, batch_size=5
            )
        self.assertEqual(summary, "本日两篇。")
        self.assertTrue(all(item.chinese_summary for item in papers))
        self.assertEqual(len(client.calls), 3)


class AnalysisSummaryTests(unittest.TestCase):
    def test_compose_summary_uses_chinese_fields(self):
        composed = analysis._compose_summary(Analysis(verdict="质量标定是关键。", result="测得 42 个星系团质量。"))
        self.assertEqual(composed, "质量标定是关键。测得 42 个星系团质量。")
        self.assertEqual(analysis._compose_summary(Analysis()), "")

    def test_english_summary_is_not_accepted_as_chinese(self):
        self.assertFalse(analysis._has_chinese("This paper measures 42 clusters."))
        self.assertTrue(analysis._has_chinese("这篇论文测量了 42 个星系团。"))


class TranslateDateTests(unittest.TestCase):
    def test_date_forms_do_not_invalidate_translation(self):
        source = "In July 2026 the team observed a nova."
        translated = "2026 年 7 月，团队观测到一颗新星。"
        self.assertFalse(numbers_supported(translated, source))
        self.assertTrue(numbers_supported_ignoring_dates(translated, source))

    def test_translate_items_keeps_text_when_only_dates_differ(self):
        item = Item(
            id="rss:nasa:1",
            kind="secondary",
            title="Nova observed",
            url="https://example.invalid/nova",
            source="nasa",
            published="2026-07-10",
            text="In July 2026 the team observed a nova.",
        )
        client = FakeClient([
            {"items": [{"id": item.id, "title_zh": "观测到新星", "text_zh": "2026 年 7 月，团队观测到一颗新星。"}]}
        ])
        translated = translate.translate_items(client, "fake", [item])
        self.assertEqual(translated[0].title_zh, "观测到新星")
        self.assertTrue(translated[0].text_zh)


class RadarOverviewTests(unittest.TestCase):
    def setUp(self):
        self.hoods = [radar.Neighborhood(id="stellar", name="恒星与银河系", categories=("astro-ph.SR",))]
        self.papers = [
            paper(id="2610.11111v1", title="Stellar flares in the Kepler field",
                  categories=["astro-ph.SR"], summary="We study flares in 2026."),
        ]
        self.block = radar.select_blocks(self.hoods, self.papers, per_field=2, max_fields=2)

    def test_rule_overview_is_a_paragraph_without_scan_phrase(self):
        self.assertIn("本期各方向新投稿", self.block.overview)
        self.assertNotIn("扫一眼", self.block.overview)
        self.assertNotIn("扫一眼", self.block.hits[0].display_summary)

    def test_model_overview_replaces_rule_paragraph(self):
        client = FakeClient([{"overview": "本周恒星方向投稿 1 篇，代表工作是 Kepler 场的耀发统计。"}])
        updated = radar.annotate_radar(client, "fake", self.block)
        self.assertEqual(updated.overview, "本周恒星方向投稿 1 篇，代表工作是 Kepler 场的耀发统计。")
        self.assertEqual(updated.basis, "model")

    def test_model_overview_with_fabricated_number_is_rejected(self):
        client = FakeClient([{"overview": "本周恒星方向投稿 987 篇。"}])
        updated = radar.annotate_radar(client, "fake", self.block)
        self.assertEqual(updated.overview, self.block.overview)


class RenderFallbackTests(unittest.TestCase):
    def test_email_shows_note_instead_of_english_as_chinese_summary(self):
        item = paper(chinese_summary="")
        block = radar.select_blocks(
            [radar.Neighborhood(id="stellar", name="恒星与银河系", categories=("astro-ph.SR",))],
            [paper(id="2610.22222v1", title="Flares", categories=["astro-ph.SR"], summary="We study flares.")],
        )
        html = render_email_html(
            "总览",
            [item],
            timezone_name="Asia/Shanghai",
            deep_papers=1,
            brief_papers=0,
            radar=block,
        )
        self.assertIn("本次未生成中文摘要", html)
        self.assertNotIn("这篇论文的英文摘要要点", html)
        self.assertIn(block.overview, html)
        self.assertNotIn("扫一眼", html)


if __name__ == "__main__":
    unittest.main()
