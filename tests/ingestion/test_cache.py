"""
tests/test_cache.py — Tests for cache short-circuit behaviour
Agent: Agent 1 (Ingestion Agent)

Tests:
  1. test_cache_hit_completes_in_under_5_seconds
  2. test_force_refresh_bypasses_cache
"""

import json
import time
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from tests.fixtures import (
    AAPL_TICKER,
    AAPL_CIK,
    CACHE_HIT_MAX_SECONDS,
)
from schemas.ingestion_schemas import IngestionSummary, VectorDbStats


def _make_valid_summary_dict() -> dict:
    """Create a valid IngestionSummary dict for cache testing."""
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
        "xbrl_tags_used": {"revenue": "RevenueFromContractWithCustomerExcludingAssessedTax"},
        "warnings": [],
        "errors": [],
        "run_status": "SUCCESS",
        "ingestion_timestamp": "2024-11-15T10:30:00+00:00",
        "ingestion_duration_sec": 142,
    }


@patch("agents.ingestion_agent.check_existing_run")
def test_cache_hit_completes_in_under_5_seconds(mock_check_existing_run):
    """
    When a valid cached run exists, run_ingestion must complete in < 5 seconds
    (Block G.4 requirement). No external API calls are made.
    """
    from agents.ingestion_agent import run_ingestion

    # Create a temporary summary JSON file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(_make_valid_summary_dict(), f)
        temp_path = Path(f.name)

    # Mock check_existing_run to return our temp file path
    mock_check_existing_run.return_value = temp_path

    try:
        start = time.monotonic()
        summary = run_ingestion(AAPL_TICKER, force_refresh=False)
        elapsed = time.monotonic() - start

        assert summary is not None, "Expected cached summary, got None"
        assert elapsed < CACHE_HIT_MAX_SECONDS, (
            f"Cache hit took {elapsed:.2f}s — must complete in < {CACHE_HIT_MAX_SECONDS}s. "
            "The cache short-circuit is not working correctly."
        )
        assert summary.run_status == "SUCCESS"
        assert summary.ticker == AAPL_TICKER

    finally:
        temp_path.unlink(missing_ok=True)


@patch("agents.ingestion_agent.check_existing_run")
@patch("agents.ingestion_agent.resolve_ticker_to_cik")
@patch("agents.ingestion_agent.delete_ticker_chunks")
def test_force_refresh_bypasses_cache(
    mock_delete_chunks,
    mock_resolve_ticker,
    mock_check_existing_run,
):
    """
    When force_refresh=True, check_existing_run must never be called
    (the cache check is skipped entirely).
    """
    from agents.ingestion_agent import run_ingestion

    # Make resolve_ticker return None so the agent halts immediately
    # (we only care that it tried to bypass cache, not that it ran to completion)
    mock_resolve_ticker.return_value = None

    try:
        result = run_ingestion(AAPL_TICKER, force_refresh=True)
    except Exception:
        pass  # We expect the agent to fail-fast since ticker resolution returns None

    # The cache check must not have been called (force_refresh bypasses it)
    mock_check_existing_run.assert_not_called()
