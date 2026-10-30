"""全文读取：默认整篇送进模型，不截取、不限页。"""

from __future__ import annotations

import unittest
from unittest import mock

from arxiv_digest.analysis import MAX_FULLTEXT_PROMPT_CHARS, _excerpt
from arxiv_digest.fulltext import DEFAULT_MAX_CHARS, DEFAULT_MAX_PAGES, _extract_pdf_text, condense


class FullTextDefaultsTests(unittest.TestCase):
    def test_defaults_mean_unlimited(self):
        self.assertEqual(DEFAULT_MAX_CHARS, 0)
        self.assertEqual(DEFAULT_MAX_PAGES, 0)
        self.assertEqual(MAX_FULLTEXT_PROMPT_CHARS, 0)

    def test_condense_zero_returns_text_unchanged(self):
        text = "x" * 200_000
        self.assertIs(condense(text, 0), text)

    def test_condense_default_returns_text_unchanged(self):
        text = "sample selection. " * 5_000
        self.assertEqual(condense(text), text)

    def test_excerpt_default_keeps_whole_pdf(self):
        text = ("Introduction. " * 5_000) + ("sample selection and covariance " * 2_000)
        self.assertEqual(_excerpt(text), text)

    def test_excerpt_with_budget_still_trims(self):
        text = ("head " * 4_000) + ("sample selection " * 500)
        self.assertLess(len(_excerpt(text, 2_000)), len(text))


class PdfPageSelectionTests(unittest.TestCase):
    class _Page:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    def _reader(self, *args, **kwargs):
        class _Reader:
            pages = [PdfPageSelectionTests._Page("p1 "), PdfPageSelectionTests._Page("p2 "),
                     PdfPageSelectionTests._Page("p3 ")]
        return _Reader()

    def test_zero_pages_means_every_page(self):
        with mock.patch("pypdf.PdfReader", self._reader):
            text = _extract_pdf_text(b"%PDF-1.4", max_pages=0)
        self.assertIn("p3", text)

    def test_positive_page_limit_still_applies(self):
        with mock.patch("pypdf.PdfReader", self._reader):
            text = _extract_pdf_text(b"%PDF-1.4", max_pages=1)
        self.assertIn("p1", text)
        self.assertNotIn("p3", text)


if __name__ == "__main__":
    unittest.main()
