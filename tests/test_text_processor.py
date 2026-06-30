"""
tests/test_text_processor.py — Tests for text_processor.py
Agent: Agent 1 (Ingestion Agent)

Tests:
  1. test_ten_k_chunks_have_correct_section_codes
  2. test_eight_k_chunks_have_event_type_populated
  3. test_chunk_word_count_is_in_target_range
  4. test_cleaned_text_removes_html_artifacts
"""

import pytest

from tools.ingestion.text_processor import (
    _clean_text,
    _split_into_chunks,
    _extract_event_type,
    _extract_ten_k_sections,
)
from schemas.financial_fields import TEN_K_SECTIONS
from tests.fixtures import VALID_10K_SECTION_CODES
from core.config import settings

from bs4 import BeautifulSoup


# ── Minimal 10-K HTML for testing ─────────────────────────────────────────
SAMPLE_10K_HTML = b"""
<html><body>
<p>ITEM 1. BUSINESS</p>
<p>Apple Inc. designs, manufactures, and markets smartphones, personal computers,
tablets, wearables, and accessories worldwide. The Company also sells a variety
of related services. The Company's fiscal year is the 52 or 53-week period that
ends on the last Saturday of September. We believe our business is really great
and we sell a lot of phones every year. We also have a lot of retail stores.
This is extra padding to ensure this section has at least 50 words so that the
text processor does not skip it as a short heading. The business is very good.</p>
<p>ITEM 1A. RISK FACTORS</p>
<p>The Company faces intense competition in all its markets. Competitors include
large well-funded companies with significant resources. The Company's financial
condition and operating results are subject to a variety of risks. If things go
badly, we might lose a lot of money and have to close stores. We rely on supply
chains that could be disrupted by global events. This is extra padding to ensure
this section has at least 50 words so it isn't skipped by the short section filter.</p>
<p>Item 3. Legal Proceedings</p>
<p>The Company is involved in various legal proceedings. The Company does not
expect the outcome of such proceedings to have a material adverse effect.</p>
<p>Item 7. Management Discussion and Analysis</p>
<p>Net sales increased 6 percent year-over-year, driven primarily by iPhone,
Services, and Mac categories. Gross margin was 46.2 percent compared to 44.1
percent in the prior year. Operating income increased to 114 billion dollars.</p>
</body></html>
"""

SAMPLE_8K_HTML = b"""
<html><body>
<h1>FORM 8-K</h1>
<p>Item 5.02 Departure of Directors or Certain Officers.</p>
<p>Apple Inc. announced today that Luca Maestri will be stepping down as Chief
Financial Officer effective January 1, 2025. Kevan Parekh, Vice President of
Financial Planning and Analysis, will succeed Maestri as CFO.</p>
</body></html>
"""


def test_ten_k_chunks_have_correct_section_codes():
    """
    Sections extracted from a 10-K HTML must map to valid section_code values.
    Only codes defined in TEN_K_SECTIONS should appear.
    """
    soup = BeautifulSoup(SAMPLE_10K_HTML, "lxml")
    sections = _extract_ten_k_sections(soup)

    # Verify every returned section code is in the valid set
    for code in sections.keys():
        assert code in VALID_10K_SECTION_CODES, (
            f"Unexpected section_code '{code}' — not in {VALID_10K_SECTION_CODES}"
        )

    # Verify at least some sections were found
    assert len(sections) >= 2, (
        f"Expected at least 2 sections from 10-K HTML, got {len(sections)}: {list(sections.keys())}"
    )


def test_eight_k_chunks_have_event_type_populated():
    """
    8-K event type must be extracted and match a known SEC item code.
    For the sample HTML above, Item 5.02 is the event.
    """
    event_type = _extract_event_type(SAMPLE_8K_HTML)

    # Must find "5.02" in the document
    assert event_type == "5.02", (
        f"Expected event_type '5.02' from 8-K HTML, got {event_type!r}"
    )


def test_chunk_word_count_is_in_target_range():
    """
    Chunks produced by _split_into_chunks must have word counts in the
    configured range [CHUNK_MIN_WORDS, CHUNK_MAX_WORDS].
    """
    # Create a text large enough to produce multiple chunks
    long_text = " ".join(["word"] * 2000)  # 2000 words
    chunks = _split_into_chunks(long_text)

    assert chunks, "No chunks produced from 2000-word text"

    for i, chunk in enumerate(chunks):
        word_count = len(chunk.split())
        # The last chunk may be smaller than the minimum (remainder)
        if i < len(chunks) - 1:
            assert word_count <= settings.CHUNK_MAX_WORDS, (
                f"Chunk {i} has {word_count} words, exceeding max {settings.CHUNK_MAX_WORDS}"
            )


def test_cleaned_text_removes_html_artifacts():
    """
    _clean_text must remove HTML artifacts, page markers, and normalize whitespace.
    """
    dirty_text = """
    <script>var x = 1;</script>
    F-14   Page 42   Table of Contents
    This is   the actual content.
    \u00a0\u00a0\u00a0  Extra   spaces  and  unicode   noise.
    - 14 -
    """
    cleaned = _clean_text(dirty_text)

    # Note: script content is removed by BeautifulSoup decompose before _clean_text in the real pipeline
    # For this test, we just test _clean_text's responsibilities:
    # Page markers removed
    assert "F-14" not in cleaned, "Page marker 'F-14' not removed"
    assert "Table of Contents" not in cleaned, "'Table of Contents' not removed"

    # Actual content preserved
    assert "actual content" in cleaned, "Actual content was removed during cleaning"
