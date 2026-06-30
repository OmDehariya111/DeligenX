"""
agents/ingestion_agent.py — Agent 1: Ingestion Agent (CrewAI Orchestration)
Agent: Agent 1 (Ingestion Agent)
Reads: SEC EDGAR APIs, yfinance, optional user file
Writes: data/deligenx.db, data/chromadb/, data/outputs/{ticker}/ingestion_summary.json

The top-level orchestrator for the Ingestion Agent. This module:
  1. Implements the cache-hit short-circuit (returns immediately if valid run exists)
  2. Calls tools in the correct 7-phase sequence
  3. Handles the parallel text pipeline + numbers pipeline
  4. Assembles the final IngestionSummary from all tool outputs
  5. Returns the summary to the pipeline entry point (run_ingestion.py)

Dependency direction: agents → tools → schemas → core
This module ONLY imports from tools, schemas, and core — never the reverse.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.chromadb_client import get_chunk_count, delete_ticker_chunks
from core.config import settings
from core.database import create_all_tables
from core.logger import AuditLogger
from schemas.ingestion_schemas import CompanyIdentity, IngestionSummary, VectorDbStats
from tools.ingestion.company_resolver import (
    check_existing_run,
    fetch_company_metadata,
    load_cached_identity,
    resolve_ticker_to_cik,
    save_identity_cache,
)
from tools.ingestion.db_writer import (
    build_ingestion_summary,
    write_financial_data,
    write_ingestion_summary,
)
from tools.ingestion.filing_fetcher import download_filing
from tools.ingestion.financial_extractor import extract_financial_data
from tools.ingestion.market_data import fetch_beta, fetch_fy_end_prices, get_fiscal_year_end_dates
from tools.ingestion.text_processor import process_eight_k, process_ten_k
from tools.ingestion.user_file_processor import process_user_file
from tools.ingestion.validator import validate_and_fill


def run_ingestion(
    ticker: str,
    file_path: Optional[Path] = None,
    force_refresh: bool = False,
) -> Optional[IngestionSummary]:
    """
    Main entry point for the Ingestion Agent.

    Runs the complete 7-phase ingestion pipeline for the given ticker symbol
    and optional user file. Returns a validated IngestionSummary.

    Cache behaviour:
      - If force_refresh=False AND a valid run exists within the cache TTL,
        the cached ingestion_summary.json is loaded and returned immediately
        without making any external API calls. This completes in under 5 seconds.
      - If force_refresh=True, all caches are bypassed and a fresh run is performed.

    Args:
        ticker: Company ticker symbol (any casing — normalized to uppercase internally)
        file_path: Optional path to a user-supplied PDF or .txt file
        force_refresh: If True, bypass all caches and re-run from scratch

    Returns:
        IngestionSummary on success, None if the ticker cannot be resolved
        (the only condition where None is returned — see Block B Contract 1)
    """
    # ── Normalize ticker (Iron Law — always normalize before any use) ──────
    ticker = ticker.upper().strip()

    # ── Initialize logger and ensure directories exist ─────────────────────
    logger = AuditLogger(agent_name="IngestionAgent", ticker=ticker)
    settings.ensure_directories()
    create_all_tables()

    logger.success("IngestionStart", f"Starting ingestion for {ticker} (force_refresh={force_refresh})")

    # ══════════════════════════════════════════════════════════════════════
    # CACHE SHORT-CIRCUIT
    # ══════════════════════════════════════════════════════════════════════
    if not force_refresh:
        existing_run = check_existing_run(ticker)
        if existing_run is not None:
            try:
                with existing_run.open("r", encoding="utf-8") as fh:
                    cached_data = json.load(fh)
                summary = IngestionSummary(**cached_data)
                logger.success(
                    "CacheHit",
                    f"Valid cached run found for {ticker} — returning immediately",
                )
                return summary
            except Exception as e:
                logger.warning("CacheHit", f"Cached summary invalid, running fresh: {e}")

    # Force refresh: delete existing ChromaDB chunks for this ticker
    if force_refresh:
        deleted = delete_ticker_chunks(ticker)
        if deleted > 0:
            logger.success("ForceRefresh", f"Deleted {deleted} stale ChromaDB chunks for {ticker}")

    run_start = time.monotonic()
    all_warnings: list[str] = []
    all_errors: list[str] = []

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1 — COMPANY IDENTITY RESOLUTION
    # ══════════════════════════════════════════════════════════════════════

    # Step 1: Resolve ticker → CIK
    resolved = resolve_ticker_to_cik(ticker, logger)
    if resolved is None:
        logger.error("Phase1", f"Ticker '{ticker}' not found in SEC EDGAR — halting")
        return None  # Only condition where None is returned (Contract 1 spec)

    cik, company_name = resolved

    # Step 2: Check identity cache, or fetch from Submissions API
    identity: Optional[CompanyIdentity] = None
    if not force_refresh:
        identity = load_cached_identity(ticker)
        if identity is not None:
            logger.success("Phase1", f"Identity loaded from cache for {ticker}")

    selected_filings = []
    if identity is None:
        result = fetch_company_metadata(cik, ticker, company_name, logger)
        if result is None:
            logger.error("Phase1", f"Submissions API failed for {ticker} — halting")
            all_errors.append(f"Submissions API unreachable for {ticker} (CIK {cik})")
            # Return PARTIAL rather than None — we have CIK from tickers map
            identity = CompanyIdentity(
                ticker=ticker,
                company_name=company_name,
                cik=cik,
                sic_code="0000",
                industry_name="Unknown",
                exchange="",
                state_of_incorp="",
                fiscal_year_end="1231",
                fiscal_year_end_month=12,
            )
        else:
            identity, selected_filings = result
            save_identity_cache(identity)

    logger.success(
        "Phase1Complete",
        f"{identity.company_name} | CIK: {identity.cik} | SIC: {identity.sic_code} | "
        f"FY end month: {identity.fiscal_year_end_month}",
    )

    # ══════════════════════════════════════════════════════════════════════
    # PHASES 2 + 3 — TEXT PIPELINE (10-K, 8-K, User File → ChromaDB)
    # ══════════════════════════════════════════════════════════════════════

    chunks_10k = 0
    chunks_8k = 0
    chunks_user_file = 0
    ten_k_filings_processed: list[str] = []
    ten_k_count = 0

    # Filter filing list by type
    ten_k_filings = [f for f in selected_filings if f.form_type in ("10-K", "10-K/A")]
    eight_k_filings = [f for f in selected_filings if f.form_type in ("8-K", "8-K/A")]

    # Process 10-K filings
    for filing in ten_k_filings:
        raw_bytes = download_filing(filing, cik, logger, ticker=ticker)
        if raw_bytes is None:
            msg = (
                f"10-K filing {filing.filing_date} (accession: {filing.accession_number}) "
                f"failed to download after {settings.EDGAR_MAX_RETRIES} retries. Skipped."
            )
            all_errors.append(msg)
            continue

        # Determine the fiscal year for this 10-K from report_date or filing_date
        fiscal_year = _infer_fiscal_year(filing, identity)

        chunk_count = process_ten_k(raw_bytes, filing, ticker, fiscal_year, logger)
        if chunk_count > 0:
            chunks_10k += chunk_count
            ten_k_filings_processed.append(filing.filing_date)
            ten_k_count += 1
        else:
            all_warnings.append(
                f"10-K {filing.filing_date}: no chunks extracted (check document format)"
            )

    # Process 8-K filings
    for filing in eight_k_filings:
        raw_bytes = download_filing(filing, cik, logger, ticker=ticker)
        if raw_bytes is None:
            msg = (
                f"8-K filing {filing.filing_date} (accession: {filing.accession_number}) "
                f"failed to download. Skipped."
            )
            all_errors.append(msg)
            continue

        chunk_count = process_eight_k(raw_bytes, filing, ticker, logger)
        chunks_8k += chunk_count

    # Phase 3: Process optional user file
    if file_path is not None:
        file_path = Path(file_path)
        chunk_count = process_user_file(file_path, ticker, logger)
        chunks_user_file = chunk_count
        if chunk_count == 0:
            all_warnings.append(f"User file '{file_path.name}' produced no storable chunks")

    logger.success(
        "TextPipelineComplete",
        f"{ticker}: {chunks_10k} 10-K chunks + {chunks_8k} 8-K chunks + "
        f"{chunks_user_file} user-file chunks = "
        f"{chunks_10k + chunks_8k + chunks_user_file} total",
    )

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 4 — FINANCIAL DATA COLLECTION (CompanyFacts + yfinance)
    # ══════════════════════════════════════════════════════════════════════

    year_data, extraction_warnings = extract_financial_data(
        cik=cik,
        ticker=ticker,
        target_years=None,  # Auto-detect from data
        logger=logger,
    )
    all_warnings.extend(extraction_warnings)

    # Enrich FinancialYearData with fiscal year end dates.
    # Primary source: CompanyFacts data (has all 5 years' exact end dates)
    # Fallback: report_dates from the selected 10-K filing records
    _populate_fy_end_dates(year_data, ten_k_filings, cik)

    # Fetch market data (stock prices + beta) from yfinance
    fy_end_dates = get_fiscal_year_end_dates(year_data)
    if fy_end_dates:
        prices = fetch_fy_end_prices(ticker, fy_end_dates, logger)
        for fy, price in prices.items():
            if fy in year_data:
                year_data[fy].stock_price_fy_end = price
                if price is not None:
                    year_data[fy].xbrl_tags_used["stock_price_fy_end"] = "yfinance:historical_close"

    beta = fetch_beta(ticker, logger)
    # Beta is a single value — apply to all years (it's a current metric)
    for fyd in year_data.values():
        if fyd.beta is None:
            fyd.beta = beta
            if beta is not None:
                fyd.xbrl_tags_used["beta"] = "yfinance:info.beta"

    logger.success("Phase4Complete", f"{ticker}: financial extraction done for {len(year_data)} years")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 5 — ARITHMETIC VALIDATION AND GAP FILLING
    # ══════════════════════════════════════════════════════════════════════

    year_data, validation_warnings, missing_fields = validate_and_fill(year_data, logger)
    all_warnings.extend(validation_warnings)

    logger.success(
        "Phase5Complete",
        f"{ticker}: validation complete — {len(missing_fields)} missing field categories",
    )

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 7 — WRITE OUTPUTS
    # ══════════════════════════════════════════════════════════════════════

    # Write financial data to SQLite
    rows_written = write_financial_data(year_data, logger)
    if rows_written == 0 and year_data:
        all_errors.append("SQLite write failed — financial_data table may be empty")

    # Build VectorDbStats
    vector_stats = VectorDbStats(
        total_chunks=chunks_10k + chunks_8k + chunks_user_file,
        chunks_10k=chunks_10k,
        chunks_8k=chunks_8k,
        chunks_user_file=chunks_user_file,
        filings_processed_10k=ten_k_filings_processed,
        filings_processed_8k=len(eight_k_filings),
    )

    # Assemble and validate IngestionSummary
    duration_sec = int(time.monotonic() - run_start)
    summary = build_ingestion_summary(
        identity=identity,
        year_data=year_data,
        missing_fields=missing_fields,
        vector_db_stats=vector_stats,
        warnings=all_warnings,
        errors=all_errors,
        ten_k_count=ten_k_count,
        duration_sec=duration_sec,
        logger=logger,
    )

    if summary is None:
        logger.error("Phase7", "Failed to build IngestionSummary — pipeline output incomplete")
        return None

    # Write summary to disk
    write_ingestion_summary(summary, logger)

    logger.success(
        "IngestionComplete",
        f"{ticker}: {summary.run_status} in {duration_sec}s | "
        f"{rows_written} DB rows | {summary.vector_db_stats.total_chunks} chunks | "
        f"{summary.fields_with_data}/45 fields populated",
    )

    return summary


def _infer_fiscal_year(filing, identity: CompanyIdentity) -> int:
    """
    Infer the fiscal year (integer) from a filing record.

    Uses report_date if available (most reliable), otherwise extracts the year
    from the filing_date. Adjusts for companies whose fiscal year ends in a
    month other than December.

    Args:
        filing: FilingRecord with filing_date and optional report_date
        identity: CompanyIdentity with fiscal_year_end_month

    Returns:
        Integer fiscal year (calendar year the FY ends)
    """
    if filing.report_date:
        try:
            from datetime import datetime
            dt = datetime.strptime(filing.report_date, "%Y-%m-%d")
            # The fiscal year integer is the calendar year the FY ends in
            return dt.year
        except ValueError:
            pass

    # Fall back to filing date year
    try:
        from datetime import datetime
        dt = datetime.strptime(filing.filing_date, "%Y-%m-%d")
        # 10-K filings are filed 60-90 days after FY end
        # If FY ends in months 10-12, the filing year = FY year
        # If FY ends in months 1-9, the filing year = FY year
        # Use fiscal_year_end_month to adjust
        fy_month = identity.fiscal_year_end_month
        filing_month = dt.month
        # If the filing is in months 1-3 and FY ends in 9-12, the FY ended last year
        if filing_month <= 3 and fy_month >= 9:
            return dt.year - 1
        return dt.year
    except ValueError:
        from datetime import datetime
        return datetime.now(timezone.utc).year


def _populate_fy_end_dates(
    year_data: dict,
    ten_k_filings: list,
    cik: str = "",
) -> None:
    """
    Populate fiscal_year_end_date in FinancialYearData for ALL collected fiscal years.

    Strategy:
      1. Load the cached CompanyFacts JSON — it contains the exact 'end' date
         for every annual entry (this covers ALL 5 years, not just the 3 selected filings).
      2. Fall back to report_date from the selected 10-K filing records.

    This fixes the bug where FY2021/FY2022 had no end date because they weren't
    in the 3-filing selected list used for text processing.

    Args:
        year_data: Dict mapping fiscal_year → FinancialYearData (mutated in place)
        ten_k_filings: List of selected 10-K FilingRecord objects (fallback)
        cik: 10-digit padded CIK (used to load CompanyFacts cache)
    """
    from datetime import datetime

    # ── Strategy 1: pull from CompanyFacts cache (covers all years) ───────
    if cik:
        cache_file = settings.cache_path() / f"{cik}_companyfacts.json"
        if cache_file.exists():
            try:
                import json
                with cache_file.open("r", encoding="utf-8") as fh:
                    cf = json.load(fh)

                us_gaap = cf.get("facts", {}).get("us-gaap", {})

                # Probe several reliable tags to get the 'end' date
                probe_tags = [
                    "Assets",
                    "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "NetIncomeLoss",
                    "Revenues",
                ]
                for tag_name in probe_tags:
                    tag_data = us_gaap.get(tag_name, {})
                    entries = tag_data.get("units", {}).get("USD", [])
                    for entry in entries:
                        if entry.get("form") != "10-K" or entry.get("fp") != "FY":
                            continue
                        fy = entry.get("fy")
                        end_date = entry.get("end", "")
                        if fy and end_date and fy in year_data:
                            if year_data[fy].fiscal_year_end_date is None:
                                year_data[fy].fiscal_year_end_date = end_date
            except Exception:
                pass  # Non-fatal — fall through to filing record fallback

    # ── Strategy 2: fall back to report_dates from selected filing records ─
    for filing in ten_k_filings:
        if filing.report_date:
            try:
                dt = datetime.strptime(filing.report_date, "%Y-%m-%d")
                fy = dt.year
                if fy in year_data and year_data[fy].fiscal_year_end_date is None:
                    year_data[fy].fiscal_year_end_date = filing.report_date
            except ValueError:
                pass
