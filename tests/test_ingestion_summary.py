"""
tests/test_ingestion_summary.py — Tests for IngestionSummary validation
Agent: Agent 1 (Ingestion Agent)

Tests:
  1. test_ingestion_summary_validates_correctly_with_all_required_fields
  2. test_ingestion_summary_rejects_invalid_run_status
  3. test_ingestion_summary_normalizes_cik_to_10_digits
"""

import pytest
from pydantic import ValidationError

from schemas.ingestion_schemas import IngestionSummary, MissingFieldEntry, VectorDbStats
from tests.fixtures import (
    AAPL_CIK,
    AAPL_TICKER,
    INGESTION_SUMMARY_REQUIRED_FIELDS,
    VALID_RUN_STATUSES,
)


def _make_valid_summary_dict() -> dict:
    """Return a dict with all required IngestionSummary fields populated."""
    return {
        "ticker": AAPL_TICKER,
        "company_name": "Apple Inc.",
        "cik": AAPL_CIK,
        "sic_code": "3571",
        "industry_name": "Electronic Computers",
        "exchange": "Nasdaq",
        "fiscal_year_end_month": 9,
        "years_covered": [2020, 2021, 2022, 2023, 2024],
        "years_missing": [],
        "fields_with_data": 38,
        "fields_missing": 7,
        "missing_critical_fields": [],
        "vector_db_stats": {
            "total_chunks": 250,
            "chunks_10k": 180,
            "chunks_8k": 70,
            "chunks_user_file": 0,
            "filings_processed_10k": ["2024-11-01", "2023-11-03", "2022-10-28"],
            "filings_processed_8k": 14,
        },
        "xbrl_tags_used": {
            "revenue": "RevenueFromContractWithCustomerExcludingAssessedTax",
            "net_income": "NetIncomeLoss",
        },
        "warnings": [],
        "errors": [],
        "run_status": "SUCCESS",
        "ingestion_timestamp": "2024-11-15T10:30:00+00:00",
        "ingestion_duration_sec": 142,
    }


def test_ingestion_summary_validates_correctly_with_all_required_fields():
    """
    A complete IngestionSummary with all required fields must pass Pydantic validation.
    All required field keys must be present in the resulting model.
    """
    summary_dict = _make_valid_summary_dict()
    summary = IngestionSummary(**summary_dict)

    for field_name in INGESTION_SUMMARY_REQUIRED_FIELDS:
        assert hasattr(summary, field_name), (
            f"IngestionSummary is missing field '{field_name}'"
        )

    assert summary.ticker == AAPL_TICKER
    assert summary.run_status == "SUCCESS"
    assert len(summary.years_covered) == 5


def test_ingestion_summary_rejects_invalid_run_status():
    """
    IngestionSummary must reject run_status values other than 'SUCCESS', 'PARTIAL', or None.
    'FAILED', 'ERROR', 'OK', etc. must all be rejected.
    """
    bad_status_values = ["FAILED", "ERROR", "OK", "COMPLETE", "done", "1"]

    for bad_status in bad_status_values:
        summary_dict = _make_valid_summary_dict()
        summary_dict["run_status"] = bad_status

        with pytest.raises(ValidationError, match=r"(?i)(run_status|status)") as exc_info:
            IngestionSummary(**summary_dict)

        # Verify the right field was flagged
        error_str = str(exc_info.value).lower()
        assert "run_status" in error_str or "partial" in error_str or "success" in error_str, (
            f"Error for status '{bad_status}' doesn't clearly identify run_status field. "
            f"Error: {str(exc_info.value)[:200]}"
        )


def test_ingestion_summary_normalizes_cik_to_10_digits():
    """
    CIK values that are less than 10 digits must be zero-padded to 10 digits.
    Input '320193' → '0000320193'.
    """
    summary_dict = _make_valid_summary_dict()
    summary_dict["cik"] = "320193"  # 6 digits — needs 4 zeros prepended

    summary = IngestionSummary(**summary_dict)

    assert summary.cik == AAPL_CIK, (
        f"Expected CIK '{AAPL_CIK}' after normalization, got '{summary.cik}'"
    )
    assert len(summary.cik) == 10, (
        f"CIK must always be 10 digits, got length {len(summary.cik)}"
    )
