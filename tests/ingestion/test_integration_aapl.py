"""
tests/test_integration_aapl.py — Integration Tests: Real AAPL Run
Agent: Agent 1 (Ingestion Agent)

IMPORTANT: These tests make REAL network calls to SEC EDGAR and yfinance.
They are slow (5–15 minutes for a full run) and require internet access.
They are skipped in unit test mode unless explicitly enabled via env var:

    DELIGENX_RUN_INTEGRATION=1 pytest tests/test_integration_aapl.py -v

Tests:
  1. test_aapl_ingestion_completes_without_critical_error
  2. test_aapl_has_at_least_4_fiscal_years_of_data
  3. test_aapl_revenue_fy2024_within_expected_range
  4. test_aapl_chromadb_has_minimum_chunk_count
  5. test_aapl_sqlite_has_rows_for_all_years
  6. test_aapl_ingestion_summary_json_exists_and_is_valid
"""

import json
import os
import pytest

# Integration tests are opt-in only
INTEGRATION_ENABLED = os.environ.get("DELIGENX_RUN_INTEGRATION", "0") == "1"

pytestmark = pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason=(
        "Integration tests make real EDGAR/yfinance network calls. "
        "Enable with: DELIGENX_RUN_INTEGRATION=1 pytest tests/test_integration_aapl.py"
    ),
)


@pytest.fixture(scope="module")
def aapl_summary():
    """
    Run the full AAPL ingestion once for all integration tests in this module.
    Scope=module means the ingestion runs once and all tests share the result.
    """
    from agents.ingestion_agent import run_ingestion
    summary = run_ingestion("AAPL", force_refresh=True)
    return summary


def test_aapl_ingestion_completes_without_critical_error(aapl_summary):
    """
    The AAPL ingestion must complete and return a non-None IngestionSummary.
    run_status must be 'SUCCESS' or 'PARTIAL' — not None.
    """
    assert aapl_summary is not None, (
        "run_ingestion returned None for AAPL — ticker resolution or critical API failure"
    )
    assert aapl_summary.run_status in ("SUCCESS", "PARTIAL"), (
        f"Expected run_status SUCCESS or PARTIAL, got {aapl_summary.run_status!r}"
    )
    from tests.fixtures import AAPL_CIK
    assert aapl_summary.cik == AAPL_CIK, (
        f"Expected CIK {AAPL_CIK}, got {aapl_summary.cik}"
    )


def test_aapl_has_at_least_4_fiscal_years_of_data(aapl_summary):
    """
    AAPL must have financial data for at least 4 fiscal years.
    Apple's EDGAR history extends back to the early 2000s — 4 years is a minimum.
    """
    from tests.fixtures import AAPL_MIN_YEARS_WITH_DATA
    years = aapl_summary.years_covered
    assert len(years) >= AAPL_MIN_YEARS_WITH_DATA, (
        f"Expected at least {AAPL_MIN_YEARS_WITH_DATA} fiscal years of data, "
        f"got {len(years)}: {years}"
    )


def test_aapl_revenue_fy2024_within_expected_range(aapl_summary):
    """
    AAPL FY2024 revenue must be within $380B–$400B (in full USD).
    This validates both XBRL extraction correctness and USD normalization.
    """
    from tests.fixtures import (
        AAPL_REVENUE_FY2024_MIN,
        AAPL_REVENUE_FY2024_MAX,
        AAPL_EXPECTED_RECENT_YEAR,
    )
    from core.database import get_connection
    from sqlalchemy import text

    # Query directly from SQLite — the single source of truth
    with get_connection() as conn:
        result = conn.execute(text("""
            SELECT revenue FROM financial_data
            WHERE ticker = 'AAPL' AND fiscal_year = :fy
        """), {"fy": AAPL_EXPECTED_RECENT_YEAR})
        row = result.fetchone()

    assert row is not None, (
        f"No financial_data row found for AAPL FY{AAPL_EXPECTED_RECENT_YEAR}"
    )

    revenue = row[0]
    assert revenue is not None, (
        f"Revenue is None for AAPL FY{AAPL_EXPECTED_RECENT_YEAR}"
    )
    assert AAPL_REVENUE_FY2024_MIN <= revenue <= AAPL_REVENUE_FY2024_MAX, (
        f"AAPL FY{AAPL_EXPECTED_RECENT_YEAR} revenue {revenue:,.0f} is outside expected range "
        f"[{AAPL_REVENUE_FY2024_MIN:,.0f}, {AAPL_REVENUE_FY2024_MAX:,.0f}]. "
        "This may indicate wrong units (millions instead of full USD)."
    )


def test_aapl_chromadb_has_minimum_chunk_count(aapl_summary):
    """
    After AAPL ingestion, ChromaDB must contain at least the minimum chunk count.
    """
    from tests.fixtures import AAPL_MIN_TOTAL_CHUNKS, AAPL_MIN_10K_CHUNKS
    from core.chromadb_client import get_chunk_count

    total = get_chunk_count(ticker="AAPL")
    assert total >= AAPL_MIN_TOTAL_CHUNKS, (
        f"Expected at least {AAPL_MIN_TOTAL_CHUNKS} AAPL chunks in ChromaDB, got {total}"
    )

    # Also verify 10-K chunks from the summary
    chunks_10k = aapl_summary.vector_db_stats.chunks_10k
    assert chunks_10k >= AAPL_MIN_10K_CHUNKS, (
        f"Expected at least {AAPL_MIN_10K_CHUNKS} 10-K chunks, got {chunks_10k}"
    )


def test_aapl_sqlite_has_rows_for_all_years(aapl_summary):
    """
    The financial_data table must contain a row for each year in years_covered.
    """
    from core.database import get_connection
    from sqlalchemy import text

    years_covered = aapl_summary.years_covered

    with get_connection() as conn:
        result = conn.execute(text("""
            SELECT fiscal_year FROM financial_data
            WHERE ticker = 'AAPL'
            ORDER BY fiscal_year
        """))
        db_years = [row[0] for row in result.fetchall()]

    for year in years_covered:
        assert year in db_years, (
            f"FY{year} is in years_covered but not found in financial_data table. "
            f"DB years: {db_years}"
        )


def test_aapl_ingestion_summary_json_exists_and_is_valid(aapl_summary):
    """
    The ingestion_summary.json file must exist on disk and be a valid JSON
    that can be re-loaded as an IngestionSummary model.
    """
    from core.config import settings
    from schemas.ingestion_schemas import IngestionSummary

    summary_path = settings.ticker_output_path("AAPL") / "ingestion_summary.json"
    assert summary_path.exists(), (
        f"ingestion_summary.json not found at {summary_path}"
    )

    # Load and re-validate
    with summary_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    reloaded = IngestionSummary(**data)
    assert reloaded.ticker == "AAPL"
    assert reloaded.run_status in ("SUCCESS", "PARTIAL")
    assert len(reloaded.years_covered) >= 4
