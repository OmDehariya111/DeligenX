"""
tests/test_company_resolver.py — Tests for company_resolver.py
Agent: Agent 1 (Ingestion Agent)

Tests:
  1. test_aapl_cik_resolves_to_0000320193
  2. test_aapl_fiscal_year_end_month_is_9
  3. test_aapl_sic_code_is_3571
  4. test_invalid_ticker_returns_none_not_exception
"""

import pytest
from unittest.mock import MagicMock, patch

from tests.fixtures import (
    AAPL_CIK,
    AAPL_FY_END_MONTH,
    AAPL_SIC_CODE,
    AAPL_TICKER,
)
from tools.ingestion.company_resolver import (
    resolve_ticker_to_cik,
    fetch_company_metadata,
)
from core.logger import AuditLogger


def _make_logger(ticker: str) -> AuditLogger:
    """Create an AuditLogger for test use."""
    return AuditLogger(agent_name="TestAgent", ticker=ticker)


# ── Sample mock data ──────────────────────────────────────────────────────
MOCK_TICKERS_MAP = {
    "0": {"cik_str": "320193", "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": "789019", "ticker": "MSFT", "title": "Microsoft Corp"},
}

MOCK_SUBMISSIONS_AAPL = {
    "name": "Apple Inc.",
    "sic": "3571",
    "sicDescription": "Electronic Computers",
    "exchanges": ["Nasdaq"],
    "stateOfIncorporation": "CA",
    "fiscalYearEnd": "0926",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-24-000123",
                "0000320193-23-000120",
                "0000320193-22-000110",
                "0000320193-24-000050",
                "0000320193-24-000060",
            ],
            "form": ["10-K", "10-K", "10-K", "8-K", "8-K"],
            "filingDate": [
                "2024-11-01",
                "2023-11-03",
                "2022-10-28",
                "2024-06-15",
                "2024-03-15",
            ],
            "reportDate": [
                "2024-09-28",
                "2023-09-30",
                "2022-09-24",
                "",
                "",
            ],
        },
        "files": [],
    },
}


@patch("tools.ingestion.company_resolver.get_json")
def test_aapl_cik_resolves_to_0000320193(mock_get_json):
    """AAPL ticker must resolve to CIK 0000320193 (10-digit zero-padded)."""
    mock_get_json.return_value = MOCK_TICKERS_MAP
    logger = _make_logger(AAPL_TICKER)

    result = resolve_ticker_to_cik(AAPL_TICKER, logger)

    assert result is not None, "resolve_ticker_to_cik returned None for AAPL"
    cik, name = result
    assert cik == AAPL_CIK, f"Expected CIK {AAPL_CIK}, got {cik}"
    assert "Apple" in name, f"Expected company name to contain 'Apple', got {name}"


@patch("tools.ingestion.company_resolver.get_json")
def test_aapl_fiscal_year_end_month_is_9(mock_get_json):
    """AAPL fiscal year end month must be 9 (September)."""
    # Mock tickers map call
    mock_get_json.return_value = MOCK_SUBMISSIONS_AAPL
    logger = _make_logger(AAPL_TICKER)

    result = fetch_company_metadata(AAPL_CIK, AAPL_TICKER, "Apple Inc.", logger)

    assert result is not None, "fetch_company_metadata returned None"
    identity, filings = result
    assert identity.fiscal_year_end_month == AAPL_FY_END_MONTH, (
        f"Expected fiscal_year_end_month={AAPL_FY_END_MONTH}, "
        f"got {identity.fiscal_year_end_month}"
    )
    # Verify the raw MMDD field starts with "09" for September
    assert identity.fiscal_year_end.startswith("09"), (
        f"Expected fiscal_year_end to start with '09', got {identity.fiscal_year_end!r}"
    )


@patch("tools.ingestion.company_resolver.get_json")
def test_aapl_sic_code_is_3571(mock_get_json):
    """AAPL SIC code must be 3571 (Electronic Computers)."""
    mock_get_json.return_value = MOCK_SUBMISSIONS_AAPL
    logger = _make_logger(AAPL_TICKER)

    result = fetch_company_metadata(AAPL_CIK, AAPL_TICKER, "Apple Inc.", logger)

    assert result is not None
    identity, _ = result
    assert identity.sic_code == AAPL_SIC_CODE, (
        f"Expected SIC code {AAPL_SIC_CODE}, got {identity.sic_code}"
    )
    assert "Computer" in identity.industry_name, (
        f"Expected industry_name to contain 'Computer', got {identity.industry_name!r}"
    )


@patch("tools.ingestion.company_resolver.get_json")
def test_invalid_ticker_returns_none_not_exception(mock_get_json):
    """An invalid ticker must return None — never raise an exception."""
    mock_get_json.return_value = MOCK_TICKERS_MAP
    logger = _make_logger("FAKEXYZ99")

    # Should not raise — must return None gracefully
    result = resolve_ticker_to_cik("FAKEXYZ99", logger)

    assert result is None, (
        f"Expected None for invalid ticker, got {result!r}"
    )
