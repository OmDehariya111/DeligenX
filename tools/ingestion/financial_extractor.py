"""
tools/ingestion/financial_extractor.py — Phase 4: CompanyFacts API Financial Extraction
Agent: Agent 1 (Ingestion Agent)
Reads: SEC EDGAR CompanyFacts API (one call per company → full JSON)
Writes: Populates FinancialYearData objects (passed to validator then db_writer)

Makes ONE API call to the CompanyFacts endpoint, downloads the entire JSON
(3–20 MB), and extracts all 39 directly-tagged financial fields for up to 5
fiscal years using the XBRL fallback tag sequences from financial_fields.py.

Computed fields (FCF, EBITDA, Net Debt, etc.) are handled by the validator.

Key rules enforced here:
  - Only entries where form == "10-K" AND fp == "FY" are used
  - Only the 5 most recent fiscal years are selected
  - All monetary values verified to be in full USD (not thousands/millions)
  - Which tag was used for each field is recorded in XbrlTagsUsed
  - Missing fields → None (never zero, never estimated)
  - Special handling for: shares_outstanding (dei taxonomy),
    short_term_debt (summation), SGA (summation), intangibles (summation)
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.config import settings
from core.http_client import get_json
from core.logger import AuditLogger
from schemas.financial_fields import FIELD_DEFINITIONS, EDGAR_FIELDS
from schemas.ingestion_schemas import FinancialYearData


def _build_companyfacts_url(cik: str) -> str:
    """
    Build the CompanyFacts API URL for a given CIK.

    Args:
        cik: 10-digit padded CIK

    Returns:
        Full URL for the CompanyFacts API endpoint
    """
    return f"{settings.EDGAR_COMPANYFACTS_BASE}/CIK{cik}.json"


def _load_or_fetch_companyfacts(cik: str, ticker: str, logger: AuditLogger) -> Optional[dict]:
    """
    Load the CompanyFacts JSON from cache or fetch from EDGAR.

    The CompanyFacts JSON is large (3–20 MB) and changes only when a new filing
    is made. It is cached to data/cache/{cik}_companyfacts.json to avoid
    repeated large downloads on re-runs.

    Args:
        cik: 10-digit padded CIK
        ticker: Uppercase ticker symbol (for logging)
        logger: AuditLogger for this run

    Returns:
        Parsed CompanyFacts JSON dict, or None on failure
    """
    cache_file = settings.cache_path() / f"{cik}_companyfacts.json"
    settings.ensure_directories()

    # Use cache if within TTL
    if cache_file.exists():
        age_days = (time.time() - cache_file.stat().st_mtime) / 86400
        if age_days <= settings.DELIGENX_CACHE_TTL_DAYS:
            try:
                t0 = time.monotonic()
                with cache_file.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                logger.success(
                    "LoadCompanyFacts",
                    f"{ticker}: loaded from cache ({cache_file.stat().st_size // 1024} KB)",
                    logger.elapsed_ms(t0),
                )
                return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("LoadCompanyFacts", f"Cache corrupted, re-fetching: {e}")

    # Fetch from EDGAR
    url = _build_companyfacts_url(cik)
    t0 = time.monotonic()
    data = get_json(url, is_edgar=True)
    duration_ms = logger.elapsed_ms(t0)

    if data is None:
        logger.error("LoadCompanyFacts", f"CompanyFacts API failed for {ticker} (CIK {cik})", duration_ms)
        return None

    # Cache the response
    try:
        with cache_file.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
        size_kb = cache_file.stat().st_size // 1024
        logger.success("LoadCompanyFacts", f"{ticker}: fetched and cached ({size_kb} KB)", duration_ms)
    except OSError as e:
        logger.warning("LoadCompanyFacts", f"Could not write CompanyFacts cache: {e}")

    return data


def _filter_annual_entries(entries: list[dict], target_years: list[int]) -> dict[int, dict]:
    """
    Filter CompanyFacts entry list to only annual 10-K values for target years.

    The CompanyFacts JSON contains entries from ALL form types (10-K, 10-Q, 8-K, etc.)
    and ALL periods (annual, quarterly, instantaneous). We need only:
      - form == "10-K"  (annual report)
      - fp == "FY"      (full fiscal year, not a quarter)

    When multiple 10-K entries exist for the same fiscal year (e.g., an amendment),
    select the most recently filed one (latest "filed" date).
    
    CRITICAL: Groups by the calendar year of the `end` date, not the SEC `fy` attribute,
    because EDGAR often tags prior-year comparisons with the document's `fy`.

    Args:
        entries: List of value entries from CompanyFacts JSON for one tag
        target_years: List of fiscal years we want data for

    Returns:
        Dict mapping fiscal_year (int) → best entry dict for that year
    """
    annual_entries = [
        e for e in entries
        if e.get("form") == "10-K" and e.get("fp") == "FY"
    ]

    best_by_year: dict[int, dict] = {}
    for entry in annual_entries:
        end_date = entry.get("end")
        if not end_date:
            continue
            
        try:
            period_year = int(end_date[:4])
        except ValueError:
            continue
            
        if period_year not in target_years:
            continue
            
        filed = entry.get("filed", "")
        if period_year not in best_by_year or filed > best_by_year[period_year].get("filed", ""):
            best_by_year[period_year] = entry

    return best_by_year


def _get_target_years(companyfacts_data: dict) -> list[int]:
    """
    Determine the 5 most recent fiscal years for which 10-K data is available.

    Scans the top-level facts for any widely-available concept (Assets) to
    determine which fiscal years have data. Falls back to the last 5 calendar
    years if the scan fails.

    Args:
        companyfacts_data: Full CompanyFacts JSON dict

    Returns:
        List of up to 5 fiscal year integers, sorted ascending (e.g., [2020,2021,2022,2023,2024])
    """
    us_gaap = companyfacts_data.get("facts", {}).get("us-gaap", {})

    # Try Assets first — virtually every company has it
    probe_tags = ["Assets", "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                  "NetIncomeLoss"]

    for probe_tag in probe_tags:
        tag_data = us_gaap.get(probe_tag, {})
        usd_entries = tag_data.get("units", {}).get("USD", [])
        annual_entries = [
            e for e in usd_entries
            if e.get("form") == "10-K" and e.get("fp") == "FY" and e.get("end")
        ]
        if annual_entries:
            years = set()
            for e in annual_entries:
                try:
                    years.add(int(e["end"][:4]))
                except ValueError:
                    pass
            if years:
                all_years = sorted(years, reverse=True)
                selected = sorted(all_years[: settings.FINANCIAL_YEARS_TO_COLLECT])
                return selected

    # Fallback: use last N calendar years
    current_year = datetime.now(timezone.utc).year
    return list(range(current_year - settings.FINANCIAL_YEARS_TO_COLLECT + 1, current_year + 1))


def _extract_single_tag(
    us_gaap: dict,
    dei: dict,
    tag: str,
    target_years: list[int],
    unit: str,
) -> dict[int, tuple[float, str]]:
    """
    Extract values for a single XBRL tag across target fiscal years.

    Searches both us-gaap and dei taxonomies. Filters to the correct unit
    (USD, USD/shares, or shares) and only annual 10-K entries.

    Args:
        us_gaap: facts["us-gaap"] section of CompanyFacts JSON
        dei: facts["dei"] section of CompanyFacts JSON
        tag: XBRL tag name to look up
        target_years: List of fiscal years to extract data for
        unit: Expected unit string ("USD", "USD/shares", "shares")

    Returns:
        Dict mapping fiscal_year → (value, tag_name) for each year found
    """
    # Try us-gaap first, then dei
    tag_data = us_gaap.get(tag) or dei.get(tag)
    if tag_data is None:
        return {}

    unit_entries = tag_data.get("units", {}).get(unit, [])
    if not unit_entries:
        return {}

    best_by_year = _filter_annual_entries(unit_entries, target_years)

    result: dict[int, tuple[float, str]] = {}
    for fy, entry in best_by_year.items():
        val = entry.get("val")
        if val is not None:
            result[fy] = (float(val), tag)

    return result


def _extract_field(
    field_name: str,
    field_def: dict,
    us_gaap: dict,
    dei: dict,
    target_years: list[int],
    logger: AuditLogger,
    ticker: str,
) -> dict[int, tuple[Optional[float], str]]:
    """
    Extract one financial field across all target years using the tag fallback sequence.

    Tries each tag in order. Uses the first tag that returns data for any year.
    For fields with special_handling, applies summation logic after tag extraction.

    Args:
        field_name: Name of the financial field
        field_def: Field definition dict from FIELD_DEFINITIONS
        us_gaap: facts["us-gaap"] section
        dei: facts["dei"] section
        target_years: Target fiscal years
        logger: AuditLogger for this run
        ticker: For logging context

    Returns:
        Dict mapping fiscal_year → (value_or_None, source_tag_string)
    """
    tags = field_def.get("tags", [])
    unit = field_def.get("unit", "USD")
    special = field_def.get("special_handling", "")

    if not tags:
        return {}  # Computed fields — handled by validator

    # ── Standard fallback: try tags in order and accumulate ───────────────
    result: dict[int, tuple[float, str]] = {}
    for tag in tags:
        values = _extract_single_tag(us_gaap, dei, tag, target_years, unit)
        for fy, (val, tag_str) in values.items():
            if fy not in result:
                result[fy] = (val, tag_str)
        # Stop early if we have found values for all target years
        if len(result) >= len(target_years):
            break

    if result:
        return result

    # ── Special handling for fields that may need summation ───────────────
    if special == "sum_goods_services":
        # Revenue: sum SalesRevenueGoodsNet + SalesRevenueServicesNet
        goods = _extract_single_tag(us_gaap, dei, "SalesRevenueGoodsNet", target_years, unit)
        services = _extract_single_tag(us_gaap, dei, "SalesRevenueServicesNet", target_years, unit)
        combined: dict[int, tuple[float, str]] = {}
        all_years = set(goods.keys()) | set(services.keys())
        for fy in all_years:
            g = goods.get(fy, (0.0, ""))[0] if fy in goods else 0.0
            s = services.get(fy, (0.0, ""))[0] if fy in services else 0.0
            if g > 0 or s > 0:
                combined[fy] = (g + s, "SalesRevenueGoodsNet+SalesRevenueServicesNet")
        if combined:
            logger.warning(field_name, f"{ticker}: revenue assembled from goods+services split")
            return {fy: (val, src) for fy, (val, src) in combined.items()}

    if special == "sum_ga_sales":
        # SGA: sum GeneralAndAdministrativeExpense + SellingAndMarketingExpense
        ga = _extract_single_tag(us_gaap, dei, "GeneralAndAdministrativeExpense", target_years, unit)
        sm = _extract_single_tag(us_gaap, dei, "SellingAndMarketingExpense", target_years, unit)
        combined = {}
        all_years = set(ga.keys()) | set(sm.keys())
        for fy in all_years:
            g_val = ga.get(fy, (0.0, ""))[0] if fy in ga else 0.0
            s_val = sm.get(fy, (0.0, ""))[0] if fy in sm else 0.0
            if g_val > 0 or s_val > 0:
                combined[fy] = (g_val + s_val, "GeneralAndAdministrativeExpense+SellingAndMarketingExpense")
        if combined:
            logger.warning(field_name, f"{ticker}: SGA assembled from G&A + Sales split")
            return {fy: (val, src) for fy, (val, src) in combined.items()}

    if special == "sum_finite_indefinite":
        # Intangibles: sum FiniteLived + IndefiniteLived if total tag absent
        finite = _extract_single_tag(us_gaap, dei, "FiniteLivedIntangibleAssetsNet", target_years, unit)
        indefinite = _extract_single_tag(us_gaap, dei, "IndefiniteLivedIntangibleAssetsExcludingGoodwill", target_years, unit)
        combined = {}
        all_years = set(finite.keys()) | set(indefinite.keys())
        for fy in all_years:
            f_val = finite.get(fy, (0.0, ""))[0] if fy in finite else 0.0
            i_val = indefinite.get(fy, (0.0, ""))[0] if fy in indefinite else 0.0
            combined[fy] = (f_val + i_val, "FiniteLivedIntangibleAssetsNet+IndefiniteLived")
        if combined:
            return {fy: (val, src) for fy, (val, src) in combined.items()}

    if special == "sum_st_debt_components":
        # Short-term debt: ShortTermBorrowings + LongTermDebtCurrent (if both found)
        stb = _extract_single_tag(us_gaap, dei, "ShortTermBorrowings", target_years, unit)
        ltd_curr = _extract_single_tag(us_gaap, dei, "LongTermDebtCurrent", target_years, unit)
        combined = {}
        all_years = set(stb.keys()) | set(ltd_curr.keys())
        for fy in all_years:
            s_val = stb.get(fy, (0.0, ""))[0] if fy in stb else 0.0
            l_val = ltd_curr.get(fy, (0.0, ""))[0] if fy in ltd_curr else 0.0
            combined[fy] = (s_val + l_val, "ShortTermBorrowings+LongTermDebtCurrent")
        if combined:
            return {fy: (val, src) for fy, (val, src) in combined.items()}
        # Fall back to DebtCurrent
        debt_curr = _extract_single_tag(us_gaap, dei, "DebtCurrent", target_years, unit)
        if debt_curr:
            return {fy: (val, src) for fy, (val, src) in debt_curr.items()}

    if special == "sum_dep_amort":
        # D&A: Depreciation + AmortizationOfIntangibleAssets
        dep = _extract_single_tag(us_gaap, dei, "Depreciation", target_years, unit)
        amort = _extract_single_tag(us_gaap, dei, "AmortizationOfIntangibleAssets", target_years, unit)
        combined = {}
        all_years = set(dep.keys()) | set(amort.keys())
        for fy in all_years:
            d_val = dep.get(fy, (0.0, ""))[0] if fy in dep else 0.0
            a_val = amort.get(fy, (0.0, ""))[0] if fy in amort else 0.0
            combined[fy] = (d_val + a_val, "Depreciation+AmortizationOfIntangibleAssets")
        if combined:
            logger.warning(field_name, f"{ticker}: D&A assembled from Depreciation + Amortization split")
            return {fy: (val, src) for fy, (val, src) in combined.items()}

    if special == "sum_common_preferred_dividends":
        # Dividends: sum common + preferred
        common = _extract_single_tag(us_gaap, dei, "PaymentsOfDividendsCommonStock", target_years, unit)
        preferred = _extract_single_tag(us_gaap, dei, "PaymentsOfDividendsPreferredStockAndPreferenceStock", target_years, unit)
        combined = {}
        all_years = set(common.keys()) | set(preferred.keys())
        for fy in all_years:
            c_val = common.get(fy, (0.0, ""))[0] if fy in common else 0.0
            p_val = preferred.get(fy, (0.0, ""))[0] if fy in preferred else 0.0
            combined[fy] = (c_val + p_val, "PaymentsOfDividendsCommonStock+Preferred")
        if combined:
            return {fy: (val, src) for fy, (val, src) in combined.items()}

    return {}  # All tags exhausted — field is missing


def extract_financial_data(
    cik: str,
    ticker: str,
    target_years: Optional[list[int]],
    logger: AuditLogger,
) -> tuple[dict[int, FinancialYearData], list[str]]:
    """
    Main extraction function: fetch CompanyFacts JSON and populate all 39 EDGAR fields.

    Makes ONE API call to the CompanyFacts endpoint, then extracts all fields
    for all target fiscal years using the fallback tag sequences.

    Args:
        cik: 10-digit padded CIK
        ticker: Uppercase ticker symbol
        target_years: List of fiscal years to extract (None = auto-detect from data)
        logger: AuditLogger for this run

    Returns:
        Tuple of:
          - Dict mapping fiscal_year → FinancialYearData (with all extracted fields)
          - List of warning strings
    """
    ticker = ticker.upper().strip()
    warnings: list[str] = []

    # ── Load CompanyFacts JSON ─────────────────────────────────────────────
    companyfacts = _load_or_fetch_companyfacts(cik, ticker, logger)
    if companyfacts is None:
        return {}, ["CompanyFacts API unavailable — no financial data collected"]

    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    dei = companyfacts.get("facts", {}).get("dei", {})
    company_name = companyfacts.get("entityName", ticker)

    # ── Determine target fiscal years ─────────────────────────────────────
    if target_years is None:
        target_years = _get_target_years(companyfacts)

    logger.success(
        "ExtractFinancialData",
        f"{ticker}: target years {target_years}",
    )

    # ── Initialize empty FinancialYearData for each target year ───────────
    year_data: dict[int, FinancialYearData] = {
        fy: FinancialYearData(
            ticker=ticker,
            cik=cik,
            company_name=company_name,
            fiscal_year=fy,
            ingestion_timestamp=datetime.now(timezone.utc).isoformat(),
        )
        for fy in target_years
    }

    # ── Extract each EDGAR field using fallback tag sequences ─────────────
    for field_name in EDGAR_FIELDS:
        field_def = FIELD_DEFINITIONS[field_name]

        # Skip fields with no tags (computed or market data)
        if not field_def.get("tags"):
            continue

        extracted = _extract_field(
            field_name, field_def, us_gaap, dei, target_years, logger, ticker
        )

        zero_if_absent = field_def.get("zero_if_absent", False)

        for fy, fyd in year_data.items():
            if fy in extracted:
                val, tag_used = extracted[fy]
                setattr(fyd, field_name, val)
                fyd.xbrl_tags_used[field_name] = tag_used
            elif zero_if_absent:
                setattr(fyd, field_name, 0.0)
                fyd.xbrl_tags_used[field_name] = "ZERO_IF_ABSENT"
            else:
                # Field is missing — store None (Iron Law 3)
                setattr(fyd, field_name, None)
                if field_def.get("critical"):
                    warn_msg = (
                        f"MISSING: {field_name} for {ticker} FY{fy} — "
                        f"tried tags: {field_def['tags']}"
                    )
                    if warn_msg not in warnings:
                        warnings.append(warn_msg)
                    logger.warning("MissingField", f"{ticker} FY{fy}: {field_name} not found")

    # ── Special handling: shares_outstanding DEI taxonomy ─────────────────
    _extract_shares_outstanding_dei(dei, year_data, target_years, logger, ticker)

    logger.success(
        "ExtractFinancialData",
        f"{ticker}: extraction complete for {len(target_years)} years",
    )

    return year_data, warnings


def _extract_shares_outstanding_dei(
    dei: dict,
    year_data: dict[int, FinancialYearData],
    target_years: list[int],
    logger: AuditLogger,
    ticker: str,
) -> None:
    """
    Special case: extract EntityCommonStockSharesOutstanding from the DEI taxonomy.

    This tag lives in facts["dei"], not facts["us-gaap"], so it needs separate
    handling. Used as a fallback when CommonStockSharesOutstanding is missing.

    Args:
        dei: facts["dei"] section of CompanyFacts JSON
        year_data: Dict of FinancialYearData to update in place
        target_years: Target fiscal years
        logger: AuditLogger for this run
        ticker: For logging context
    """
    dei_tag = "EntityCommonStockSharesOutstanding"
    tag_data = dei.get(dei_tag, {})
    shares_entries = tag_data.get("units", {}).get("shares", [])

    if not shares_entries:
        return

    best_by_year = _filter_annual_entries(shares_entries, target_years)

    for fy, entry in best_by_year.items():
        if fy not in year_data:
            continue
        fyd = year_data[fy]
        # Only use DEI tag as fallback — don't overwrite us-gaap value
        if fyd.shares_outstanding is None:
            val = entry.get("val")
            if val is not None:
                fyd.shares_outstanding = float(val)
                fyd.xbrl_tags_used["shares_outstanding"] = f"dei:{dei_tag}"
                logger.success(
                    "ExtractSharesDEI",
                    f"{ticker} FY{fy}: shares from dei:{dei_tag}",
                )
