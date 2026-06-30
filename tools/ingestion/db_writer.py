"""
tools/ingestion/db_writer.py — Phase 7: SQLite Write and Ingestion Summary Generation
Agent: Agent 1 (Ingestion Agent)
Reads: FinancialYearData objects, MissingFieldEntry list, VectorDbStats
Writes: data/deligenx.db (financial_data table)
        data/outputs/{ticker}/ingestion_summary.json

Responsibilities:
  1. Upsert all FinancialYearData records into the financial_data SQLite table
  2. Assemble the complete IngestionSummary (Contract 1) from all collected data
  3. Validate IngestionSummary via Pydantic before writing to disk
  4. Write ingestion_summary.json to data/outputs/{ticker}/

All SQL is parameterized (Iron Law — no f-string SQL).
Uses INSERT OR REPLACE for safe upsert (enabled by UNIQUE(ticker, fiscal_year)).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from core.config import settings
from core.database import create_all_tables, get_connection
from core.logger import AuditLogger
from schemas.ingestion_schemas import (
    CompanyIdentity,
    FinancialYearData,
    IngestionSummary,
    MissingFieldEntry,
    VectorDbStats,
)


def write_financial_data(
    year_data: dict[int, FinancialYearData],
    logger: AuditLogger,
) -> int:
    """
    Upsert all FinancialYearData records into the financial_data SQLite table.

    Uses INSERT OR REPLACE (SQLite upsert) — safe because of the
    UNIQUE(ticker, fiscal_year) constraint. Running the same ingestion twice
    overwrites with the latest data rather than creating duplicates.

    Args:
        year_data: Dict mapping fiscal_year → FinancialYearData
        logger: AuditLogger for this run

    Returns:
        Number of rows successfully written
    """
    if not year_data:
        logger.warning("WriteFinancialData", "No year data to write")
        return 0

    create_all_tables()
    rows_written = 0

    # Build the INSERT OR REPLACE statement with all 52 column placeholders
    # Using named parameters (:column_name) — fully parameterized, never f-string
    insert_sql = text("""
        INSERT OR REPLACE INTO financial_data (
            ticker, cik, company_name, fiscal_year, fiscal_year_end_date, form_type,
            revenue, cost_of_revenue, gross_profit, sga_expense, rd_expense,
            operating_income, interest_expense, income_before_tax, income_tax_expense,
            net_income, eps_basic, eps_diluted, non_operating_income,
            total_assets, current_assets, cash_and_equivalents, short_term_investments,
            accounts_receivable, inventory, ppe_net, goodwill, intangible_assets,
            total_liabilities, current_liabilities, accounts_payable, short_term_debt,
            long_term_debt_noncurrent, total_equity, retained_earnings,
            shares_outstanding, weighted_avg_shares_basic, weighted_avg_shares_diluted,
            operating_cash_flow, capex, depreciation_amortization, free_cash_flow,
            investing_cash_flow, financing_cash_flow, dividends_paid, stock_buybacks,
            ebitda, net_debt, working_capital,
            stock_price_fy_end, beta, market_cap,
            xbrl_tags_used, data_source_notes, ingestion_timestamp
        ) VALUES (
            :ticker, :cik, :company_name, :fiscal_year, :fiscal_year_end_date, :form_type,
            :revenue, :cost_of_revenue, :gross_profit, :sga_expense, :rd_expense,
            :operating_income, :interest_expense, :income_before_tax, :income_tax_expense,
            :net_income, :eps_basic, :eps_diluted, :non_operating_income,
            :total_assets, :current_assets, :cash_and_equivalents, :short_term_investments,
            :accounts_receivable, :inventory, :ppe_net, :goodwill, :intangible_assets,
            :total_liabilities, :current_liabilities, :accounts_payable, :short_term_debt,
            :long_term_debt_noncurrent, :total_equity, :retained_earnings,
            :shares_outstanding, :weighted_avg_shares_basic, :weighted_avg_shares_diluted,
            :operating_cash_flow, :capex, :depreciation_amortization, :free_cash_flow,
            :investing_cash_flow, :financing_cash_flow, :dividends_paid, :stock_buybacks,
            :ebitda, :net_debt, :working_capital,
            :stock_price_fy_end, :beta, :market_cap,
            :xbrl_tags_used, :data_source_notes, :ingestion_timestamp
        )
    """)

    with get_connection() as conn:
        for fy, fyd in sorted(year_data.items()):
            try:
                row_dict = fyd.to_db_dict()
                conn.execute(insert_sql, row_dict)
                rows_written += 1
                logger.success(
                    "WriteFinancialData",
                    f"{fyd.ticker} FY{fy}: {fyd.count_populated()}/45 fields populated",
                )
            except Exception as e:
                logger.error(
                    "WriteFinancialData",
                    f"{fyd.ticker} FY{fy}: DB write failed: {e}",
                )

    return rows_written


def build_ingestion_summary(
    identity: CompanyIdentity,
    year_data: dict[int, FinancialYearData],
    missing_fields: list[MissingFieldEntry],
    vector_db_stats: VectorDbStats,
    warnings: list[str],
    errors: list[str],
    ten_k_count: int,
    duration_sec: int,
    logger: AuditLogger,
) -> Optional[IngestionSummary]:
    """
    Assemble and validate the complete Ingestion Summary Report (Contract 1).

    Collects all data produced during the ingestion run and packages it into
    the IngestionSummary Pydantic model. Validates via Pydantic before returning.

    Args:
        identity: CompanyIdentity with company metadata
        year_data: Dict of all FinancialYearData objects
        missing_fields: List of MissingFieldEntry from validator
        vector_db_stats: ChromaDB chunk statistics
        warnings: All warning strings accumulated during the run
        errors: All error strings accumulated during the run
        ten_k_count: Number of 10-K filings successfully processed
        duration_sec: Total ingestion duration in seconds
        logger: AuditLogger for this run

    Returns:
        Validated IngestionSummary or None if validation fails
    """
    # Aggregate XBRL tags used across all years (take the first year's version
    # as the representative — tags should be consistent across years)
    combined_tags: dict[str, str] = {}
    for fyd in year_data.values():
        for field, tag in fyd.xbrl_tags_used.items():
            if field not in combined_tags:
                combined_tags[field] = tag

    # Count fields with data and fields missing
    all_field_values: dict[str, set] = {}
    financial_field_names = [
        "revenue", "cost_of_revenue", "gross_profit", "sga_expense", "rd_expense",
        "operating_income", "interest_expense", "income_before_tax", "income_tax_expense",
        "net_income", "eps_basic", "eps_diluted", "non_operating_income",
        "total_assets", "current_assets", "cash_and_equivalents", "short_term_investments",
        "accounts_receivable", "inventory", "ppe_net", "goodwill", "intangible_assets",
        "total_liabilities", "current_liabilities", "accounts_payable", "short_term_debt",
        "long_term_debt_noncurrent", "total_equity", "retained_earnings",
        "shares_outstanding", "weighted_avg_shares_basic", "weighted_avg_shares_diluted",
        "operating_cash_flow", "capex", "depreciation_amortization", "free_cash_flow",
        "investing_cash_flow", "financing_cash_flow", "dividends_paid", "stock_buybacks",
        "ebitda", "net_debt", "working_capital", "stock_price_fy_end", "beta", "market_cap",
    ]

    fields_with_data = 0
    fields_missing = 0
    for field_name in financial_field_names:
        has_any_data = any(
            getattr(fyd, field_name, None) is not None
            for fyd in year_data.values()
        )
        if has_any_data:
            fields_with_data += 1
        else:
            fields_missing += 1

    # Count errors that are CRITICAL (10-K failures) vs non-critical (8-K failures)
    critical_errors = [e for e in errors if "10-K filing" in e and "failed to download" in e]

    # Determine run status:
    # SUCCESS: got 3+ years of financial data + 3+ 10-K filings downloaded, no critical errors
    # PARTIAL: got some data but missing 10-K years or had critical errors
    if len(year_data) >= 3 and ten_k_count >= 3 and not critical_errors:
        run_status: Optional[str] = "SUCCESS"
    elif year_data:
        run_status = "PARTIAL"
    else:
        run_status = "PARTIAL"

    years_covered = sorted(year_data.keys())
    all_possible_years = list(range(
        min(years_covered) if years_covered else 2020,
        (max(years_covered) if years_covered else 2024) + 1
    ))
    years_missing = [y for y in all_possible_years if y not in years_covered]

    try:
        summary = IngestionSummary(
            ticker=identity.ticker,
            company_name=identity.company_name,
            cik=identity.cik,
            sic_code=identity.sic_code,
            industry_name=identity.industry_name,
            exchange=identity.exchange,
            fiscal_year_end_month=identity.fiscal_year_end_month,
            years_covered=years_covered,
            years_missing=years_missing,
            fields_with_data=fields_with_data,
            fields_missing=fields_missing,
            missing_critical_fields=missing_fields,
            vector_db_stats=vector_db_stats,
            xbrl_tags_used=combined_tags,
            warnings=warnings,
            errors=errors,
            run_status=run_status,
            ingestion_timestamp=datetime.now(timezone.utc).isoformat(),
            ingestion_duration_sec=duration_sec,
        )
        return summary

    except Exception as e:
        logger.error("BuildSummary", f"IngestionSummary validation failed: {e}")
        return None


def write_ingestion_summary(
    summary: IngestionSummary,
    logger: AuditLogger,
) -> Optional[Path]:
    """
    Write the validated IngestionSummary to disk as JSON.

    File path: data/outputs/{ticker}/ingestion_summary.json

    Args:
        summary: Validated IngestionSummary to write
        logger: AuditLogger for this run

    Returns:
        Path to the written file, or None on failure
    """
    output_dir = settings.ticker_output_path(summary.ticker)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "ingestion_summary.json"

    try:
        summary_dict = summary.model_dump()
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(summary_dict, fh, indent=2, default=str)

        logger.success(
            "WriteSummary",
            f"Ingestion summary written: {output_path} "
            f"({output_path.stat().st_size // 1024} KB)",
        )
        return output_path

    except OSError as e:
        logger.error("WriteSummary", f"Failed to write summary to {output_path}: {e}")
        return None
