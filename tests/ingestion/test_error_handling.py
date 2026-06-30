"""
tests/test_error_handling.py — Tests for graceful error handling
Agent: Agent 1 (Ingestion Agent)

Tests:
  1. test_download_failure_returns_none_not_exception
  2. test_companyfacts_api_failure_returns_empty_dict_not_exception
  3. test_validator_handles_all_none_fields_without_crashing
  4. test_http_client_returns_none_on_404
"""

import pytest
from unittest.mock import patch, MagicMock

from core.logger import AuditLogger
from tools.ingestion.filing_fetcher import download_filing
from tools.ingestion.financial_extractor import extract_financial_data
from tools.ingestion.validator import validate_and_fill
from schemas.ingestion_schemas import FilingRecord, FinancialYearData
from tests.fixtures import AAPL_CIK, AAPL_TICKER


def _make_logger(ticker: str = AAPL_TICKER) -> AuditLogger:
    """Create an AuditLogger for test use."""
    return AuditLogger(agent_name="TestErrorHandler", ticker=ticker)


def _make_empty_fyd(fy: int = 2024) -> FinancialYearData:
    """Create a FinancialYearData with all financial fields set to None."""
    return FinancialYearData(
        ticker=AAPL_TICKER,
        cik=AAPL_CIK,
        company_name="Apple Inc.",
        fiscal_year=fy,
    )


def _make_test_filing() -> FilingRecord:
    """Create a test FilingRecord."""
    return FilingRecord(
        accession_number="0000000000-00-000000",
        form_type="10-K",
        filing_date="2024-11-01",
        report_date="2024-09-28",
    )


@patch("tools.ingestion.filing_fetcher.get")
def test_download_failure_returns_none_not_exception(mock_get):
    """
    When the EDGAR Archives index page cannot be fetched, download_filing must
    return None gracefully without raising any exception.
    """
    mock_get.return_value = None  # Simulate network failure
    logger = _make_logger()
    filing = _make_test_filing()

    # Must not raise — must return None
    result = download_filing(filing, AAPL_CIK, logger)

    assert result is None, (
        f"Expected None on download failure, got {type(result).__name__}"
    )


@patch("tools.ingestion.financial_extractor._load_or_fetch_companyfacts")
def test_companyfacts_api_failure_returns_empty_dict_not_exception(mock_fetch):
    """
    When the CompanyFacts API fails (returns None), extract_financial_data
    must return an empty dict and a non-empty warnings list — never raise.
    """
    mock_fetch.return_value = None  # Simulate API failure
    logger = _make_logger()

    # Must not raise
    year_data, warnings = extract_financial_data(
        cik=AAPL_CIK,
        ticker=AAPL_TICKER,
        target_years=[2024],
        logger=logger,
    )

    assert isinstance(year_data, dict), (
        "Expected empty dict on CompanyFacts failure, got non-dict"
    )
    assert len(year_data) == 0, (
        f"Expected empty year_data on API failure, got {len(year_data)} entries"
    )
    assert len(warnings) > 0, (
        "Expected at least one warning when CompanyFacts API fails"
    )


def test_validator_handles_all_none_fields_without_crashing():
    """
    validate_and_fill must handle FinancialYearData with ALL fields as None
    without raising any exception. This is the worst-case scenario (API offline).
    """
    fyd = _make_empty_fyd()
    year_data = {2024: fyd}
    logger = _make_logger()

    # Must not raise
    updated_data, warnings, missing_entries = validate_and_fill(year_data, logger)

    assert isinstance(updated_data, dict), "validate_and_fill must return a dict"
    assert isinstance(warnings, list), "validate_and_fill must return warnings list"
    assert isinstance(missing_entries, list), "validate_and_fill must return missing_entries list"

    # Verify that missing fields are reported (since all are None)
    assert len(missing_entries) > 0, (
        "Expected missing_entries to be non-empty when all fields are None"
    )


@patch("core.http_client.requests.get")
def test_http_client_returns_none_on_404(mock_requests_get):
    """
    The HTTP client must return None (not raise) when EDGAR returns HTTP 404.
    This handles the common case of a filing not being found.
    """
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_requests_get.return_value = mock_response

    from core.http_client import get
    result = get("https://www.sec.gov/Archives/edgar/data/0/000000000/nonexistent.htm")

    assert result is None, (
        f"Expected None for HTTP 404 response, got {result!r}"
    )
