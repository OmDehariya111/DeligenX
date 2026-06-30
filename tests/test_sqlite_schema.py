"""
tests/test_sqlite_schema.py — Tests for SQLite financial_data table schema
Agent: Agent 1 (Ingestion Agent)

Tests:
  1. test_financial_data_table_exists_after_create_all
  2. test_financial_data_table_has_all_required_columns
  3. test_financial_data_table_has_unique_ticker_fiscal_year_constraint
"""

import pytest

from core.database import (
    create_all_tables,
    get_column_names,
    get_connection,
    table_exists,
)
from sqlalchemy import text
from tests.fixtures import (
    FINANCIAL_DATA_REQUIRED_COLUMNS,
    FINANCIAL_DATA_TABLE_NAME,
)


@pytest.fixture(autouse=True)
def ensure_tables():
    """Ensure the database tables exist before each test in this module."""
    create_all_tables()


def test_financial_data_table_exists_after_create_all():
    """The financial_data table must exist after create_all_tables() is called."""
    assert table_exists(FINANCIAL_DATA_TABLE_NAME), (
        f"Table '{FINANCIAL_DATA_TABLE_NAME}' does not exist in the database"
    )


def test_financial_data_table_has_all_required_columns():
    """The financial_data table must have all 52 required columns."""
    actual_columns = get_column_names(FINANCIAL_DATA_TABLE_NAME)

    missing = [col for col in FINANCIAL_DATA_REQUIRED_COLUMNS if col not in actual_columns]
    extra = [col for col in actual_columns if col not in FINANCIAL_DATA_REQUIRED_COLUMNS]

    assert not missing, (
        f"financial_data table is missing columns: {missing}"
    )
    # Note: we don't fail on extra columns — future agents may add columns
    # But we do assert the count matches to catch accidental deletions
    assert len(actual_columns) >= len(FINANCIAL_DATA_REQUIRED_COLUMNS), (
        f"Expected at least {len(FINANCIAL_DATA_REQUIRED_COLUMNS)} columns, "
        f"got {len(actual_columns)}"
    )


def test_financial_data_table_has_unique_ticker_fiscal_year_constraint():
    """
    The financial_data table must enforce UNIQUE(ticker, fiscal_year).

    This is verified by attempting to insert two rows with the same
    ticker + fiscal_year, which should trigger a constraint violation
    (or be handled by INSERT OR REPLACE without creating a duplicate).
    """
    with get_connection() as conn:
        # Insert a test row
        conn.execute(text("""
            INSERT OR REPLACE INTO financial_data (ticker, cik, company_name, fiscal_year)
            VALUES ('TEST_UQCHECK', '0000000001', 'Test Company', 9999)
        """))

        # Insert again with the same ticker + fiscal_year (different company_name)
        conn.execute(text("""
            INSERT OR REPLACE INTO financial_data (ticker, cik, company_name, fiscal_year)
            VALUES ('TEST_UQCHECK', '0000000001', 'Updated Company', 9999)
        """))

        # Verify only ONE row exists for this ticker + fiscal_year
        result = conn.execute(text("""
            SELECT COUNT(*) FROM financial_data
            WHERE ticker = 'TEST_UQCHECK' AND fiscal_year = 9999
        """))
        count = result.fetchone()[0]

    assert count == 1, (
        f"Expected exactly 1 row after INSERT OR REPLACE, got {count}. "
        "UNIQUE(ticker, fiscal_year) constraint may not be working."
    )

    # Cleanup test data
    with get_connection() as conn:
        conn.execute(text(
            "DELETE FROM financial_data WHERE ticker = 'TEST_UQCHECK'"
        ))
