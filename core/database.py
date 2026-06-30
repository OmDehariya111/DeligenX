"""
core/database.py — DeligenX SQLAlchemy Core Database Manager
Agent: Agent 1 creates financial_data. Agents 2-5 create their own tables.
Reads: Nothing on startup (creates tables if they don't exist)
Writes: data/deligenx.db (creates if absent)

ALL database access in this project goes through this module.
  - Engine is a module-level singleton (created once, reused everywhere)
  - Tables are defined as SQLAlchemy Table objects (NOT ORM models)
  - get_connection() is a context manager for safe connection handling
  - All SQL is parameterized — never f-string or %-format queries

Cross-cutting rule: No agent drops or modifies a table it did not create.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    REAL,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Connection, Engine

from core.config import settings


def _make_engine() -> Engine:
    """
    Create and configure the SQLAlchemy engine for the SQLite database.

    WAL journal mode is enabled for better concurrent read performance.
    Foreign keys are enforced at the connection level.
    """
    db_path = settings.db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        """Enable WAL mode and foreign key enforcement on every new connection."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


# ── Module-level engine singleton ─────────────────────────────────────────
engine: Engine = _make_engine()
metadata = MetaData()

# ── Table Definitions ─────────────────────────────────────────────────────
# IMPORTANT: Only add tables that this module (Agent 1) owns.
# Agents 2–5 define and own their own tables in their respective db modules.

financial_data = Table(
    "financial_data",
    metadata,

    # Primary key and identity
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ticker", Text, nullable=False),
    Column("cik", Text, nullable=False),
    Column("company_name", Text, nullable=False),
    Column("fiscal_year", Integer, nullable=False),
    Column("fiscal_year_end_date", Text),          # ISO date e.g. "2024-09-28"
    Column("form_type", Text),                      # "10-K"

    # ── GROUP A: Income Statement (13 fields) ─────────────────────────────
    Column("revenue", REAL),
    Column("cost_of_revenue", REAL),
    Column("gross_profit", REAL),
    Column("sga_expense", REAL),
    Column("rd_expense", REAL),
    Column("operating_income", REAL),
    Column("interest_expense", REAL),
    Column("income_before_tax", REAL),
    Column("income_tax_expense", REAL),
    Column("net_income", REAL),
    Column("eps_basic", REAL),
    Column("eps_diluted", REAL),
    Column("non_operating_income", REAL),

    # ── GROUP B: Balance Sheet (19 fields, including 2 weighted avg shares) ──
    Column("total_assets", REAL),
    Column("current_assets", REAL),
    Column("cash_and_equivalents", REAL),
    Column("short_term_investments", REAL),
    Column("accounts_receivable", REAL),
    Column("inventory", REAL),
    Column("ppe_net", REAL),
    Column("goodwill", REAL),
    Column("intangible_assets", REAL),
    Column("total_liabilities", REAL),
    Column("current_liabilities", REAL),
    Column("accounts_payable", REAL),
    Column("short_term_debt", REAL),
    Column("long_term_debt_noncurrent", REAL),
    Column("total_equity", REAL),
    Column("retained_earnings", REAL),
    Column("shares_outstanding", REAL),
    Column("weighted_avg_shares_basic", REAL),
    Column("weighted_avg_shares_diluted", REAL),

    # ── GROUP C: Cash Flow Statement (8 fields) ───────────────────────────
    Column("operating_cash_flow", REAL),
    Column("capex", REAL),
    Column("depreciation_amortization", REAL),
    Column("free_cash_flow", REAL),
    Column("investing_cash_flow", REAL),
    Column("financing_cash_flow", REAL),
    Column("dividends_paid", REAL),
    Column("stock_buybacks", REAL),

    # ── GROUP D: Derived / Computed (3 fields) ────────────────────────────
    Column("ebitda", REAL),
    Column("net_debt", REAL),
    Column("working_capital", REAL),

    # ── GROUP E: Market Data (3 fields) ───────────────────────────────────
    Column("stock_price_fy_end", REAL),
    Column("beta", REAL),
    Column("market_cap", REAL),

    # ── Audit columns ─────────────────────────────────────────────────────
    Column("xbrl_tags_used", Text),        # JSON blob: {field: tag_or_formula}
    Column("data_source_notes", Text),     # JSON blob: field-level warnings
    Column("ingestion_timestamp", Text),   # ISO 8601

    # Uniqueness constraint — enables safe INSERT OR REPLACE upsert
    UniqueConstraint("ticker", "fiscal_year", name="uq_ticker_fiscal_year"),
)


def create_all_tables() -> None:
    """
    Create all Agent 1-owned tables in the SQLite database if they do not exist.
    Safe to call multiple times (idempotent — CREATE TABLE IF NOT EXISTS).
    """
    metadata.create_all(engine, checkfirst=True)


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """
    Context manager that yields a SQLAlchemy Core connection.

    Usage:
        with get_connection() as conn:
            result = conn.execute(text("SELECT * FROM financial_data WHERE ticker = :t"),
                                  {"t": "AAPL"})

    The connection is committed on clean exit and rolled back on exception.
    """
    with engine.connect() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def table_exists(table_name: str) -> bool:
    """
    Check whether a table with the given name exists in the database.

    Args:
        table_name: Name of the table to check

    Returns:
        True if the table exists, False otherwise
    """
    with get_connection() as conn:
        result = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=:name"
            ),
            {"name": table_name},
        )
        return result.fetchone() is not None


def get_column_names(table_name: str) -> list[str]:
    """
    Return all column names for a given table.

    Args:
        table_name: Name of the table to inspect

    Returns:
        List of column name strings, empty list if table does not exist
    """
    with get_connection() as conn:
        result = conn.execute(text(f"PRAGMA table_info({table_name})"))
        rows = result.fetchall()
        return [row[1] for row in rows]
