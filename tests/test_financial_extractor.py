"""
tests/test_financial_extractor.py — Tests for financial_extractor.py
Agent: Agent 1 (Ingestion Agent)

Tests:
  1. test_revenue_field_extracted_for_at_least_4_years
  2. test_net_income_field_is_present
  3. test_xbrl_tags_used_has_entries_for_revenue_and_net_income
  4. test_monetary_values_are_in_full_usd_not_millions
  5. test_missing_field_stored_as_none_not_zero
"""

import pytest
from unittest.mock import patch

from core.logger import AuditLogger
from tools.ingestion.financial_extractor import extract_financial_data
from tests.fixtures import AAPL_CIK, AAPL_TICKER, XBRL_TAGS_REQUIRED_FIELDS


def _make_logger(ticker: str) -> AuditLogger:
    """Create an AuditLogger for test use."""
    return AuditLogger(agent_name="TestExtractor", ticker=ticker)


# ── Minimal mock CompanyFacts JSON for AAPL ────────────────────────────────
# Covers FY2022–FY2024 for revenue and net income (minimum needed to pass tests)
MOCK_COMPANYFACTS = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "label": "Revenue",
                "units": {
                    "USD": [
                        {"end": "2024-09-28", "val": 391035000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
                        {"end": "2023-09-30", "val": 383285000000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03"},
                        {"end": "2022-09-24", "val": 394328000000, "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2022-10-28"},
                        {"end": "2021-09-25", "val": 365817000000, "fy": 2021, "fp": "FY", "form": "10-K", "filed": "2021-10-29"},
                        {"end": "2020-09-26", "val": 274515000000, "fy": 2020, "fp": "FY", "form": "10-K", "filed": "2020-10-30"},
                        # Quarterly entries — must be filtered out
                        {"end": "2024-06-29", "val": 85777000000, "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2024-08-01"},
                    ]
                },
            },
            "NetIncomeLoss": {
                "label": "Net Income",
                "units": {
                    "USD": [
                        {"end": "2024-09-28", "val": 93736000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
                        {"end": "2023-09-30", "val": 96995000000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03"},
                        {"end": "2022-09-24", "val": 99803000000, "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2022-10-28"},
                        {"end": "2021-09-25", "val": 94680000000, "fy": 2021, "fp": "FY", "form": "10-K", "filed": "2021-10-29"},
                        {"end": "2020-09-26", "val": 57411000000, "fy": 2020, "fp": "FY", "form": "10-K", "filed": "2020-10-30"},
                    ]
                },
            },
            "Assets": {
                "label": "Total Assets",
                "units": {
                    "USD": [
                        {"end": "2024-09-28", "val": 364980000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
                        {"end": "2023-09-30", "val": 352583000000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03"},
                    ]
                },
            },
        },
        "dei": {},
    },
}


@patch("tools.ingestion.financial_extractor._load_or_fetch_companyfacts")
def test_revenue_field_extracted_for_at_least_4_years(mock_fetch):
    """Revenue must be extracted for at least 4 fiscal years from the CompanyFacts JSON."""
    mock_fetch.return_value = MOCK_COMPANYFACTS
    logger = _make_logger(AAPL_TICKER)

    year_data, warnings = extract_financial_data(
        cik=AAPL_CIK,
        ticker=AAPL_TICKER,
        target_years=[2020, 2021, 2022, 2023, 2024],
        logger=logger,
    )

    years_with_revenue = [
        fy for fy, fyd in year_data.items() if fyd.revenue is not None
    ]
    assert len(years_with_revenue) >= 4, (
        f"Expected revenue for at least 4 years, got {len(years_with_revenue)}: "
        f"{years_with_revenue}"
    )


@patch("tools.ingestion.financial_extractor._load_or_fetch_companyfacts")
def test_net_income_field_is_present(mock_fetch):
    """Net income must be extracted and be a positive value for at least one AAPL year."""
    mock_fetch.return_value = MOCK_COMPANYFACTS
    logger = _make_logger(AAPL_TICKER)

    year_data, _ = extract_financial_data(
        cik=AAPL_CIK,
        ticker=AAPL_TICKER,
        target_years=[2024],
        logger=logger,
    )

    assert 2024 in year_data, "FY2024 data not found in year_data"
    fyd_2024 = year_data[2024]
    assert fyd_2024.net_income is not None, "net_income is None for AAPL FY2024"
    assert fyd_2024.net_income > 0, (
        f"Expected positive net_income for AAPL FY2024, got {fyd_2024.net_income}"
    )


@patch("tools.ingestion.financial_extractor._load_or_fetch_companyfacts")
def test_xbrl_tags_used_has_entries_for_revenue_and_net_income(mock_fetch):
    """xbrl_tags_used must have entries for both 'revenue' and 'net_income'."""
    mock_fetch.return_value = MOCK_COMPANYFACTS
    logger = _make_logger(AAPL_TICKER)

    year_data, _ = extract_financial_data(
        cik=AAPL_CIK,
        ticker=AAPL_TICKER,
        target_years=[2024],
        logger=logger,
    )

    assert 2024 in year_data
    tags_used = year_data[2024].xbrl_tags_used

    for required_field in XBRL_TAGS_REQUIRED_FIELDS:
        assert required_field in tags_used, (
            f"xbrl_tags_used missing entry for '{required_field}'. "
            f"Available keys: {list(tags_used.keys())}"
        )
        assert tags_used[required_field], (
            f"xbrl_tags_used['{required_field}'] is empty"
        )


@patch("tools.ingestion.financial_extractor._load_or_fetch_companyfacts")
def test_monetary_values_are_in_full_usd_not_millions(mock_fetch):
    """
    All monetary values must be stored in full USD.
    Apple's revenue is ~$391B — values in millions would be ~391,000.
    We verify revenue > 1_000_000_000 (1 billion) to confirm full-USD storage.
    """
    mock_fetch.return_value = MOCK_COMPANYFACTS
    logger = _make_logger(AAPL_TICKER)

    year_data, _ = extract_financial_data(
        cik=AAPL_CIK,
        ticker=AAPL_TICKER,
        target_years=[2024],
        logger=logger,
    )

    fyd_2024 = year_data.get(2024)
    assert fyd_2024 is not None

    # Apple's revenue is ~$391 billion — must be > $1 billion to confirm full USD
    assert fyd_2024.revenue > 1_000_000_000, (
        f"Revenue appears to be in millions or smaller units. "
        f"Expected > $1B, got {fyd_2024.revenue:,.0f}. "
        "Values must be stored in full USD (not thousands or millions)."
    )


@patch("tools.ingestion.financial_extractor._load_or_fetch_companyfacts")
def test_missing_field_stored_as_none_not_zero(mock_fetch):
    """
    A field that is absent from the CompanyFacts JSON must be stored as None,
    not as 0. Substituting zero for missing data violates Iron Law 3.
    """
    mock_fetch.return_value = MOCK_COMPANYFACTS
    logger = _make_logger(AAPL_TICKER)

    year_data, _ = extract_financial_data(
        cik=AAPL_CIK,
        ticker=AAPL_TICKER,
        target_years=[2024],
        logger=logger,
    )

    fyd_2024 = year_data.get(2024)
    assert fyd_2024 is not None

    # 'interest_expense' is not in our mock data — should be None, not 0
    # (Apple's interest expense is notoriously tricky to extract)
    # If it happened to be found via a fallback tag, it would be a float.
    # But it must NOT be 0 if it wasn't found.
    assert fyd_2024.interest_expense is None or isinstance(fyd_2024.interest_expense, float), (
        "interest_expense must be None (not found) or a float (found) — never zero as a substitute"
    )

    # Verify the field is not 0 unless genuinely tagged as zero
    if "interest_expense" not in fyd_2024.xbrl_tags_used:
        assert fyd_2024.interest_expense is None, (
            f"interest_expense is {fyd_2024.interest_expense} but no XBRL tag was recorded. "
            "Missing data must be stored as None, not as a fabricated value."
        )
