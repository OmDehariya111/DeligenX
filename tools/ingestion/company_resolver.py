"""
tools/ingestion/company_resolver.py — Phase 1: Company Identity Resolution
Agent: Agent 1 (Ingestion Agent)
Reads: SEC company_tickers.json (cached), SEC Submissions API
Writes: data/cache/{ticker}_identity.json (company identity cache)

Responsibilities:
  1. Resolve ticker → CIK via SEC company_tickers.json file
  2. Fetch company metadata from Submissions API (SIC, FY end, exchange, etc.)
  3. Extract and filter the filing list (3 most recent 10-K + all 8-K last 2 years)
  4. Cache identity data with TTL (avoids repeated API calls)
  5. Check whether a valid ingestion run already exists (force_refresh logic)

Returns None (not raises) if the ticker is not found — the agent halts gracefully.
"""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core.config import settings
from core.http_client import get_json
from core.logger import AuditLogger
from schemas.ingestion_schemas import CompanyIdentity, FilingRecord


# ── SIC code → industry name lookup (top-level categories) ───────────────
# A subset of the most common SIC ranges. Full lookup falls back to the raw code.
_SIC_DESCRIPTIONS: dict[str, str] = {
    "0100": "Crops", "0200": "Livestock", "0700": "Agricultural Services",
    "1000": "Metal Mining", "1040": "Gold and Silver Ores Mining",
    "1311": "Crude Petroleum and Natural Gas", "1382": "Oil and Gas Field Services",
    "1500": "General Building Contractors", "1600": "Heavy Construction",
    "2000": "Food and Kindred Products", "2100": "Tobacco Products",
    "2600": "Paper and Allied Products", "2700": "Printing and Publishing",
    "2800": "Chemicals and Allied Products", "2830": "Drugs",
    "2911": "Petroleum Refining", "3310": "Steel Works and Rolling Mills",
    "3559": "Special Industry Machinery", "3571": "Electronic Computers",
    "3572": "Computer Storage Devices", "3576": "Computer Communications Equipment",
    "3577": "Computer Peripheral Equipment", "3600": "Electronic Equipment",
    "3661": "Telephone and Telegraph Apparatus", "3669": "Communications Equipment",
    "3672": "Printed Circuit Boards", "3674": "Semiconductors",
    "3812": "Defense Electronics", "3825": "Instruments for Measuring",
    "4210": "Trucking and Warehousing", "4400": "Water Transportation",
    "4512": "Air Transportation — Scheduled", "4810": "Telephone Communications",
    "4813": "Telephone Communications No Radio Telephone",
    "4899": "Communications Services", "4911": "Electric Services",
    "4941": "Water Supply", "5040": "Professional Equipment",
    "5045": "Computers and Peripherals Wholesale", "5065": "Electronic Parts Wholesale",
    "5200": "Retail Building Materials", "5311": "Department Stores",
    "5331": "Variety Stores", "5411": "Grocery Stores",
    "5600": "Apparel and Accessory Stores", "5700": "Furniture Stores",
    "5900": "Retail Stores", "5912": "Drug Stores",
    "6020": "State Commercial Banks", "6022": "National Commercial Banks",
    "6035": "Savings Institution Federally Chartered",
    "6141": "Personal Credit Institutions", "6159": "Federal-Sponsored Credit Agencies",
    "6200": "Security and Commodity Brokers", "6282": "Investment Advice",
    "6311": "Life Insurance", "6321": "Accident and Health Insurance",
    "6411": "Insurance Agents", "6512": "Operators of Apartment Buildings",
    "6552": "Land Subdividers and Developers", "7011": "Hotels and Motels",
    "7200": "Laundry Cleaning Garment Services", "7372": "Prepackaged Software",
    "7371": "Computer Programming Services", "7374": "Computer Processing and Data Prep",
    "7380": "Miscellaneous Business Services", "7389": "Services — Misc Business",
    "7812": "Motion Picture Production", "8000": "Health Services",
    "8011": "Offices and Clinics of Doctors", "8049": "Offices of Other Health Practitioners",
    "8051": "Skilled Nursing Care Facilities", "8062": "Hospitals",
    "8200": "Educational Services", "8711": "Engineering Services",
    "8731": "Commercial Physical Research", "9995": "Nonclassifiable",
}


def _sic_to_industry(sic_code: str) -> str:
    """
    Return a human-readable industry name for a given SIC code.

    Attempts exact match first, then looks up by 3-digit prefix, then 2-digit.
    Falls back to 'SIC {code}' if no match found.

    Args:
        sic_code: 4-digit SIC code string

    Returns:
        Human-readable industry description
    """
    if sic_code in _SIC_DESCRIPTIONS:
        return _SIC_DESCRIPTIONS[sic_code]
    # Try 3-digit prefix
    for prefix_len in (3, 2):
        prefix = sic_code[:prefix_len]
        for code, name in _SIC_DESCRIPTIONS.items():
            if code.startswith(prefix):
                return name
    return f"SIC {sic_code}"


def _load_tickers_map(logger: AuditLogger) -> Optional[dict]:
    """
    Download (or load from cache) the SEC company_tickers.json file.

    The tickers map is cached for 24 hours — it changes rarely. If the cache
    is fresh, no network call is made. If the download fails, returns None.

    Args:
        logger: AuditLogger for this run

    Returns:
        Dict mapping numeric index → {cik_str, title, ticker} or None on failure
    """
    cache_file = settings.cache_path() / "company_tickers.json"
    settings.ensure_directories()

    # Use cached version if it exists and is less than 24 hours old
    if cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < 24:
            try:
                with cache_file.open("r", encoding="utf-8") as fh:
                    logger.success("LoadTickersMap", "Loaded from cache (age: %.1fh)" % age_hours)
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass  # Re-download if cache is corrupt

    t0 = time.monotonic()
    data = get_json(settings.EDGAR_TICKERS_URL, is_edgar=True)
    duration_ms = logger.elapsed_ms(t0)

    if data is None:
        logger.error("LoadTickersMap", "Failed to download company_tickers.json", duration_ms)
        return None

    # Save to cache
    try:
        with cache_file.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError as e:
        logger.warning("LoadTickersMap", f"Could not write cache: {e}")

    logger.success("LoadTickersMap", f"Downloaded {len(data)} tickers", duration_ms)
    return data


def resolve_ticker_to_cik(ticker: str, logger: AuditLogger) -> Optional[tuple[str, str]]:
    """
    Resolve a ticker symbol to a (cik, company_name) tuple.

    Downloads and searches the SEC company_tickers.json file. The CIK is
    zero-padded to 10 digits.

    Args:
        ticker: Company ticker symbol (any casing — normalized internally)
        logger: AuditLogger for this run

    Returns:
        Tuple of (10-digit-padded-cik, company_name) or None if not found
    """
    ticker_upper = ticker.upper().strip()
    t0 = time.monotonic()

    tickers_map = _load_tickers_map(logger)
    if tickers_map is None:
        return None

    for _idx, entry in tickers_map.items():
        if isinstance(entry, dict) and entry.get("ticker", "").upper() == ticker_upper:
            raw_cik = str(entry.get("cik_str", entry.get("cik", "")))
            padded_cik = raw_cik.zfill(10)
            company_name = entry.get("title", "Unknown")
            logger.success(
                "ResolveTickerToCIK",
                f"{ticker_upper} → CIK {padded_cik} ({company_name})",
                logger.elapsed_ms(t0),
            )
            return padded_cik, company_name

    logger.error(
        "ResolveTickerToCIK",
        f"Ticker '{ticker_upper}' not found in SEC company_tickers.json",
        logger.elapsed_ms(t0),
    )
    return None


def fetch_company_metadata(
    cik: str,
    ticker: str,
    company_name: str,
    logger: AuditLogger,
) -> Optional[tuple[CompanyIdentity, list[FilingRecord]]]:
    """
    Fetch company metadata and complete filing list from the SEC Submissions API.

    Makes one API call to https://data.sec.gov/submissions/CIK{cik}.json.
    Extracts company identity fields and filters the filing list to:
      - 3 most recent 10-K filings (for text extraction → ChromaDB)
      - All 8-K filings from the last 2 years (for event processing → ChromaDB)

    Args:
        cik: 10-digit zero-padded CIK
        ticker: Uppercase ticker symbol
        company_name: Company name from tickers map (may be overridden by Submissions API)
        logger: AuditLogger for this run

    Returns:
        Tuple of (CompanyIdentity, list[FilingRecord]) or None on API failure
    """
    url = f"{settings.EDGAR_SUBMISSIONS_BASE}/CIK{cik}.json"
    t0 = time.monotonic()

    data = get_json(url, is_edgar=True)
    duration_ms = logger.elapsed_ms(t0)

    if data is None:
        logger.error("FetchSubmissions", f"Submissions API unreachable for CIK {cik}", duration_ms)
        return None

    # ── Extract company identity ───────────────────────────────────────────
    official_name = data.get("name", company_name)
    sic_code = str(data.get("sic", "0000")).zfill(4)
    industry_name = _sic_to_industry(sic_code)
    exchange = data.get("exchanges", [""])[0] if data.get("exchanges") else ""
    state_of_incorp = data.get("stateOfIncorporation", "")
    fy_end_raw = data.get("fiscalYearEnd", "1231")  # MMDD string, default Dec 31

    # Normalize fiscal_year_end — ensure it is a 4-char MMDD string
    fy_end_normalized = str(fy_end_raw).zfill(4)[:4]
    fy_end_month = int(fy_end_normalized[:2])

    # ── Extract and filter filing list ────────────────────────────────────
    recent_filings = data.get("filings", {}).get("recent", {})
    filing_list = _parse_filing_list(recent_filings, logger)

    # Also handle "files" pagination for companies with very long filing histories
    extra_files = data.get("filings", {}).get("files", [])
    if extra_files:
        for file_entry in extra_files:
            file_url = f"{settings.EDGAR_SUBMISSIONS_BASE}/{file_entry.get('name', '')}"
            extra_data = get_json(file_url, is_edgar=True)
            if extra_data:
                extra_filings = _parse_filing_list(extra_data, logger)
                filing_list.extend(extra_filings)

    # Filter to what we need
    ten_k_filings = _select_ten_k_filings(filing_list, logger)
    eight_k_filings = _select_eight_k_filings(filing_list, logger)
    selected_filings = ten_k_filings + eight_k_filings

    logger.success(
        "FetchSubmissions",
        f"{official_name}: {len(ten_k_filings)} 10-K + {len(eight_k_filings)} 8-K selected",
        duration_ms,
    )

    identity = CompanyIdentity(
        ticker=ticker,
        company_name=official_name,
        cik=cik,
        sic_code=sic_code,
        industry_name=industry_name,
        exchange=exchange,
        state_of_incorp=state_of_incorp,
        fiscal_year_end=fy_end_normalized,
        fiscal_year_end_month=fy_end_month,
    )

    return identity, selected_filings


def _parse_filing_list(recent_filings: dict, logger: AuditLogger) -> list[FilingRecord]:
    """
    Parse the raw Submissions API filing data into a list of FilingRecord objects.

    The Submissions API returns filing data as parallel arrays: accessionNumber[],
    form[], filingDate[], reportDate[], etc. This function zips them into records.

    Args:
        recent_filings: The 'recent' dict from the Submissions API response
        logger: AuditLogger for this run

    Returns:
        List of FilingRecord objects (all form types, unfiltered)
    """
    accessions = recent_filings.get("accessionNumber", [])
    forms = recent_filings.get("form", [])
    filing_dates = recent_filings.get("filingDate", [])
    report_dates = recent_filings.get("reportDate", [])

    records: list[FilingRecord] = []
    for i in range(len(accessions)):
        try:
            record = FilingRecord(
                accession_number=accessions[i] if i < len(accessions) else "",
                form_type=forms[i] if i < len(forms) else "",
                filing_date=filing_dates[i] if i < len(filing_dates) else "",
                report_date=report_dates[i] if i < len(report_dates) and report_dates[i] else None,
            )
            records.append(record)
        except Exception as e:
            logger.warning("ParseFilingList", f"Skipping malformed filing record at index {i}: {e}")

    return records


def _select_ten_k_filings(
    all_filings: list[FilingRecord],
    logger: AuditLogger,
) -> list[FilingRecord]:
    """
    Select the N most recent 10-K filings from the complete filing list.

    N is controlled by settings.FILINGS_10K_TO_PROCESS (default 3).

    Args:
        all_filings: Complete unfiltered filing list
        logger: AuditLogger for this run

    Returns:
        List of the N most recent 10-K FilingRecord objects, newest first
    """
    # Note: We intentionally exclude "10-K/A" because amendments often only contain 
    # Part III info (0 chunks) and would displace actual 10-Ks from our top-N limit.
    ten_ks = [f for f in all_filings if f.form_type == "10-K"]
    # Sort by filing date descending (most recent first)
    ten_ks.sort(key=lambda f: f.filing_date, reverse=True)
    selected = ten_ks[: settings.FILINGS_10K_TO_PROCESS]

    logger.success(
        "Select10KFilings",
        f"Selected {len(selected)} of {len(ten_ks)} available 10-K filings",
    )
    return selected


def _select_eight_k_filings(
    all_filings: list[FilingRecord],
    logger: AuditLogger,
) -> list[FilingRecord]:
    """
    Select all 8-K filings from the last N years.

    N is controlled by settings.FILINGS_8K_LOOKBACK_YEARS (default 2).

    Args:
        all_filings: Complete unfiltered filing list
        logger: AuditLogger for this run

    Returns:
        List of 8-K FilingRecord objects from the lookback period, newest first
    """
    cutoff_date = (
        datetime.now(timezone.utc) - timedelta(days=365 * settings.FILINGS_8K_LOOKBACK_YEARS)
    ).strftime("%Y-%m-%d")

    eight_ks = [
        f for f in all_filings
        if f.form_type in ("8-K", "8-K/A") and f.filing_date >= cutoff_date
    ]
    eight_ks.sort(key=lambda f: f.filing_date, reverse=True)

    logger.success(
        "Select8KFilings",
        f"Selected {len(eight_ks)} 8-K filings since {cutoff_date}",
    )
    return eight_ks


def load_cached_identity(ticker: str) -> Optional[CompanyIdentity]:
    """
    Load a cached CompanyIdentity from disk if it exists and is within TTL.

    Args:
        ticker: Uppercase ticker symbol

    Returns:
        CompanyIdentity if a valid cache exists, None otherwise
    """
    cache_file = settings.cache_path() / f"{ticker.upper()}_identity.json"
    if not cache_file.exists():
        return None

    age_days = (time.time() - cache_file.stat().st_mtime) / 86400
    if age_days > settings.DELIGENX_CACHE_TTL_DAYS:
        return None

    try:
        with cache_file.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return CompanyIdentity(**data)
    except (json.JSONDecodeError, OSError, Exception):
        return None


def save_identity_cache(identity: CompanyIdentity) -> None:
    """
    Save a CompanyIdentity to the local cache.

    Args:
        identity: Validated CompanyIdentity to cache
    """
    settings.ensure_directories()
    cache_file = settings.cache_path() / f"{identity.ticker}_identity.json"
    try:
        with cache_file.open("w", encoding="utf-8") as fh:
            json.dump(identity.model_dump(), fh, indent=2)
    except OSError:
        pass  # Cache write failure is non-fatal


def check_existing_run(ticker: str) -> Optional[Path]:
    """
    Check whether a valid ingestion run exists for this ticker within the cache TTL.

    Args:
        ticker: Uppercase ticker symbol

    Returns:
        Path to ingestion_summary.json if a valid run exists and is within TTL.
        None if no valid run exists or the run is too old.
    """
    summary_path = settings.ticker_output_path(ticker) / "ingestion_summary.json"
    if not summary_path.exists():
        return None

    age_days = (time.time() - summary_path.stat().st_mtime) / 86400
    if age_days > settings.DELIGENX_CACHE_TTL_DAYS:
        return None

    return summary_path
