"""引文回查与 Stage A 事实保留的回归测试。

背景：真实全文中，模型抄的 quote 常与 PDF 抽取文本差在断词、连字符、
全半角与页码；早先的严格子串比对会把正确引文判成编造，导致整张精读卡
"全文节选未检出"。
"""

import unittest

from arxiv_digest.analysis import _keep_fact, _one_line_conclusion, _strip_missing_notes
from arxiv_digest.evidence import quote_in_source
from arxiv_digest.models import Fact

SOURCE = (
    "All images were reduced using the standard VBI reduction pipeline, and "
    "the inflation-\ninduced systematic was corrected. The seeing measured "
    "with the ﬁeld sensor was stable. We selected three quiet Sun patches "
    "with 668.4 nm data."
)


class QuoteInSourceTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        self.assertTrue(quote_in_source("All images were reduced", SOURCE))

    def test_hyphenated_line_break_still_matches(self) -> None:
        self.assertTrue(quote_in_source("the inflation-induced systematic was corrected", SOURCE))

    def test_ligature_still_matches(self) -> None:
        self.assertTrue(quote_in_source("the field sensor was stable", SOURCE))

    def test_paraphrase_is_rejected(self) -> None:
        self.assertFalse(quote_in_source("images reduced by the standard pipeline", SOURCE))

    def test_too_short_quote_is_rejected(self) -> None:
        self.assertFalse(quote_in_source("the", SOURCE))


class KeepFactTests(unittest.TestCase):
    def test_value_numbers_may_come_from_elsewhere_in_fulltext(self) -> None:
        # 值里带 668.4，但 quote 只截了不含数字的一句——这正是真实卡片全空的成因。
        fact = Fact(value="三组可见光数据，含 668.4 nm 谱线", quote="We selected three quiet Sun patches")
        kept = _keep_fact(fact, SOURCE)
        self.assertEqual(kept.value, "三组可见光数据，含 668.4 nm 谱线")

    def test_bare_string_fact_with_supported_number_is_kept(self) -> None:
        kept = _keep_fact(Fact(value="样本为 668.4 nm 波段的静日区图像", quote=None), SOURCE)
        self.assertTrue(kept.value)

    def test_bare_string_fact_with_invented_number_is_dropped(self) -> None:
        kept = _keep_fact(Fact(value="样本量为 12345 个", quote=None), SOURCE)
        self.assertIsNone(kept.value)

    def test_missing_quote_is_dropped(self) -> None:
        kept = _keep_fact(Fact(value="数据来自某个巡天", quote="This sentence is not in the paper at all"), SOURCE)
        self.assertIsNone(kept.value)

    def test_unverifiable_clause_is_salvaged(self) -> None:
        fact = Fact(
            value="处理流程标准；样本量 12345 个；图像已做平场改正",
            quote="All images were reduced using the standard VBI reduction pipeline",
        )
        kept = _keep_fact(fact, SOURCE)
        self.assertIn("处理流程标准", kept.value or "")
        self.assertNotIn("12345", kept.value or "")

    def test_empty_fact_stays_empty(self) -> None:
        self.assertIsNone(_keep_fact(Fact(), SOURCE).value)


class ConclusionLineTests(unittest.TestCase):
    def test_short_text_is_untouched(self) -> None:
        self.assertEqual(_one_line_conclusion("样本为 24 个近邻星系。"), "样本为 24 个近邻星系。")

    def test_long_text_cuts_at_sentence_end(self) -> None:
        text = "a" * 40 + "。" + "b" * 100
        self.assertEqual(_one_line_conclusion(text), "a" * 40 + "。")

    def test_long_text_without_sentence_end_gets_ellipsis(self) -> None:
        text = "x" * 200
        out = _one_line_conclusion(text)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 71)

    def test_cut_prefers_comma_over_mid_word(self) -> None:
        text = "前半句说清楚结论，" + "y" * 100
        out = _one_line_conclusion(text)
        self.assertTrue(out.endswith("…"))
        self.assertNotIn("y", out)


class StripMissingNoteTests(unittest.TestCase):
    def test_note_inside_sentence_is_removed(self) -> None:
        cleaned = _strip_missing_notes("样本为 24 个星系。恒星质量区间全文节选未检出。")
        self.assertNotIn("全文节选未检出", cleaned)
        self.assertIn("样本为 24 个星系", cleaned)

    def test_note_only_becomes_empty(self) -> None:
        self.assertEqual(_strip_missing_notes("全文节选未检出"), "")


if __name__ == "__main__":
    unittest.main()
