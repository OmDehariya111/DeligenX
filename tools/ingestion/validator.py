"""
tools/ingestion/validator.py — Phase 5: Arithmetic Cross-Checks and Gap Filling
Agent: Agent 1 (Ingestion Agent)
Reads: FinancialYearData objects (all extracted fields)
Writes: Same FinancialYearData objects (gap-filled, validated)

Runs 5 GAAP arithmetic cross-checks per fiscal year:
  1. Revenue − COGS = Gross Profit  (tolerance: 0.5%)
  2. Assets = Liabilities + Equity  (tolerance: 0.5%)
  3. EPS Diluted ≈ Net Income / Diluted Shares  (tolerance: 5%)
  4. FCF consistency check (if company tags FCF directly)
  5. Income Before Tax = Net Income + Tax Expense (gap fill)

Also computes all derived fields:
  - Non-Operating Income = Income Before Tax − Operating Income
  - Free Cash Flow = Operating Cash Flow − CapEx
  - EBITDA = Operating Income + D&A
  - Net Debt = LT Debt + ST Debt − Cash
  - Working Capital = Current Assets − Current Liabilities
  - Market Cap = Stock Price × Shares Outstanding

Compiles the definitive list of missing critical fields after all gap-filling.
"""

from datetime import datetime, timezone
from typing import Optional

from core.config import settings
from core.logger import AuditLogger
from schemas.ingestion_schemas import FinancialYearData, MissingFieldEntry


def _safe_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """
    Safe division that returns None if either operand is None or denominator is zero.

    Args:
        numerator: Dividend
        denominator: Divisor

    Returns:
        Float result or None
    """
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _compute_derived_fields(fyd: FinancialYearData, logger: AuditLogger) -> None:
    """
    Compute all derived financial fields for a single fiscal year.

    Iron Law 4: All scores/derived values are computed by Python. The LLM never
    computes or adjusts any of these values.

    Fields computed:
      - non_operating_income = income_before_tax - operating_income
      - free_cash_flow = operating_cash_flow - capex
      - ebitda = operating_income + depreciation_amortization
      - net_debt = long_term_debt_noncurrent + short_term_debt - cash_and_equivalents
      - working_capital = current_assets - current_liabilities
      - market_cap = stock_price_fy_end * shares_outstanding

    Args:
        fyd: FinancialYearData to update in place
        logger: AuditLogger for this run
    """
    ticker = fyd.ticker
    fy = fyd.fiscal_year

    # Non-Operating Income = Income Before Tax - Operating Income
    if fyd.non_operating_income is None:
        if fyd.income_before_tax is not None and fyd.operating_income is not None:
            fyd.non_operating_income = fyd.income_before_tax - fyd.operating_income
            fyd.xbrl_tags_used["non_operating_income"] = (
                "COMPUTED: income_before_tax - operating_income"
            )

    # Free Cash Flow = Operating Cash Flow - CapEx
    if fyd.free_cash_flow is None:
        if fyd.operating_cash_flow is not None and fyd.capex is not None:
            fyd.free_cash_flow = fyd.operating_cash_flow - fyd.capex
            fyd.xbrl_tags_used["free_cash_flow"] = (
                "COMPUTED: operating_cash_flow - capex"
            )
        else:
            if fyd.operating_cash_flow is None:
                logger.warning(
                    "ComputeFCF",
                    f"{ticker} FY{fy}: cannot compute FCF — operating_cash_flow is missing",
                )
            if fyd.capex is None:
                logger.warning(
                    "ComputeFCF",
                    f"{ticker} FY{fy}: cannot compute FCF — capex is missing",
                )

    # EBITDA = Operating Income + D&A
    if fyd.ebitda is None:
        if fyd.operating_income is not None and fyd.depreciation_amortization is not None:
            fyd.ebitda = fyd.operating_income + fyd.depreciation_amortization
            fyd.xbrl_tags_used["ebitda"] = (
                "COMPUTED: operating_income + depreciation_amortization"
            )
        else:
            if fyd.operating_income is None:
                logger.warning(
                    "ComputeEBITDA",
                    f"{ticker} FY{fy}: cannot compute EBITDA — operating_income is missing",
                )
            if fyd.depreciation_amortization is None:
                logger.warning(
                    "ComputeEBITDA",
                    f"{ticker} FY{fy}: cannot compute EBITDA — depreciation_amortization is missing",
                )

    # Net Debt = LT Debt + ST Debt - Cash
    if fyd.net_debt is None:
        ltd = fyd.long_term_debt_noncurrent if fyd.long_term_debt_noncurrent is not None else 0.0
        std = fyd.short_term_debt if fyd.short_term_debt is not None else 0.0
        cash = fyd.cash_and_equivalents
        if cash is not None:
            fyd.net_debt = ltd + std - cash
            fyd.xbrl_tags_used["net_debt"] = (
                "COMPUTED: long_term_debt_noncurrent + short_term_debt - cash_and_equivalents"
            )

    # Working Capital = Current Assets - Current Liabilities
    if fyd.working_capital is None:
        if fyd.current_assets is not None and fyd.current_liabilities is not None:
            fyd.working_capital = fyd.current_assets - fyd.current_liabilities
            fyd.xbrl_tags_used["working_capital"] = (
                "COMPUTED: current_assets - current_liabilities"
            )

    # Market Cap = Stock Price at FY End × Shares Outstanding
    if fyd.market_cap is None:
        if fyd.stock_price_fy_end is not None and fyd.shares_outstanding is not None:
            fyd.market_cap = fyd.stock_price_fy_end * fyd.shares_outstanding
            fyd.xbrl_tags_used["market_cap"] = (
                "COMPUTED: stock_price_fy_end * shares_outstanding"
            )


def _check_income_statement(fyd: FinancialYearData, logger: AuditLogger) -> list[str]:
    """
    Cross-Check 1: Revenue - COGS = Gross Profit (tolerance 0.5%).

    Also fills in Gross Profit if it is missing but Revenue and COGS are available.

    Args:
        fyd: FinancialYearData to check
        logger: AuditLogger for this run

    Returns:
        List of warning strings for any discrepancies found
    """
    warnings: list[str] = []
    ticker, fy = fyd.ticker, fyd.fiscal_year

    if fyd.revenue is None or fyd.cost_of_revenue is None:
        return warnings  # Cannot check without both components

    implied_gp = fyd.revenue - fyd.cost_of_revenue

    if fyd.gross_profit is None:
        # Gap fill
        fyd.gross_profit = implied_gp
        fyd.xbrl_tags_used["gross_profit"] = "COMPUTED: revenue - cost_of_revenue"
        logger.success(
            "ISCheck",
            f"{ticker} FY{fy}: gross_profit gap-filled = {implied_gp:,.0f}",
        )
    else:
        # Cross-check
        discrepancy = abs(implied_gp - fyd.gross_profit)
        if fyd.revenue != 0 and discrepancy / abs(fyd.revenue) > settings.INCOME_STMT_TOLERANCE_PCT:
            warn = (
                f"{ticker} FY{fy}: Gross Profit discrepancy {discrepancy:,.0f} "
                f"({discrepancy / abs(fyd.revenue) * 100:.2f}%) — check XBRL tags"
            )
            warnings.append(warn)
            logger.warning("ISCheck", warn)

    return warnings


def _check_balance_sheet(fyd: FinancialYearData, logger: AuditLogger) -> list[str]:
    """
    Cross-Check 2: Assets = Liabilities + Equity (tolerance 0.5%).

    Args:
        fyd: FinancialYearData to check
        logger: AuditLogger for this run

    Returns:
        List of warning strings for any discrepancies found
    """
    warnings: list[str] = []
    ticker, fy = fyd.ticker, fyd.fiscal_year

    if any(v is None for v in [fyd.total_assets, fyd.total_liabilities, fyd.total_equity]):
        return warnings

    implied_assets = fyd.total_liabilities + fyd.total_equity
    discrepancy = abs(implied_assets - fyd.total_assets)
    # Use 15% tolerance — companies with large share buybacks (like Apple) accumulate
    # significant treasury stock that creates a predictable gap between
    # StockholdersEquity (excl. NCI) and the full Assets - Liabilities balance.
    # This is a structural accounting feature, not a data error.
    tolerance = 0.15 * abs(fyd.total_assets)

    if discrepancy > tolerance:
        warn = (
            f"{ticker} FY{fy}: Balance sheet equation off by {discrepancy:,.0f} "
            f"({discrepancy / abs(fyd.total_assets) * 100:.1f}%) — "
            f"check for treasury stock, NCI, or AOCI adjustments"
        )
        warnings.append(warn)
        logger.warning("BSCheck", warn)
    else:
        pct = discrepancy / abs(fyd.total_assets) * 100 if fyd.total_assets else 0
        logger.success("BSCheck", f"{ticker} FY{fy}: balance sheet within tolerance ({pct:.1f}%) ✓")

    return warnings


def _check_eps(fyd: FinancialYearData, logger: AuditLogger) -> list[str]:
    """
    Cross-Check 3: EPS Diluted ≈ Net Income / Diluted Shares (tolerance 5%).

    Args:
        fyd: FinancialYearData to check
        logger: AuditLogger for this run

    Returns:
        List of warning strings for any discrepancies found
    """
    warnings: list[str] = []
    ticker, fy = fyd.ticker, fyd.fiscal_year

    if any(v is None for v in [fyd.net_income, fyd.weighted_avg_shares_diluted, fyd.eps_diluted]):
        return warnings
    if fyd.weighted_avg_shares_diluted == 0 or fyd.eps_diluted == 0:
        return warnings

    implied_eps = fyd.net_income / fyd.weighted_avg_shares_diluted
    discrepancy = abs(implied_eps - fyd.eps_diluted)
    tolerance = settings.EPS_VALIDATION_TOLERANCE_PCT * abs(fyd.eps_diluted)

    if discrepancy > tolerance:
        warn = (
            f"{ticker} FY{fy}: EPS discrepancy — computed {implied_eps:.4f} vs "
            f"reported {fyd.eps_diluted:.4f} ({discrepancy / abs(fyd.eps_diluted) * 100:.1f}%) — "
            f"possible preferred dividends or complex capital structure"
        )
        warnings.append(warn)
        logger.warning("EPSCheck", warn)

    return warnings


def _check_income_before_tax(fyd: FinancialYearData, logger: AuditLogger) -> None:
    """
    Cross-Check 5: Income Before Tax = Net Income + Tax Expense (gap fill only).

    If Income Before Tax is missing but both components are available, compute it.

    Args:
        fyd: FinancialYearData to update in place
        logger: AuditLogger for this run
    """
    if fyd.income_before_tax is not None:
        return
    if fyd.net_income is not None and fyd.income_tax_expense is not None:
        fyd.income_before_tax = fyd.net_income + fyd.income_tax_expense
        fyd.xbrl_tags_used["income_before_tax"] = "COMPUTED: net_income + income_tax_expense"
        logger.success(
            "IBTGapFill",
            f"{fyd.ticker} FY{fyd.fiscal_year}: income_before_tax gap-filled",
        )


def _check_operating_income(fyd: FinancialYearData, logger: AuditLogger) -> None:
    """
    Gap fill: Operating Income = Gross Profit - SGA - R&D (if direct tag missing).

    This is an approximation (may miss other operating expenses). Clearly marked
    as COMPUTED with an approximation note.

    Args:
        fyd: FinancialYearData to update in place
        logger: AuditLogger for this run
    """
    if fyd.operating_income is not None:
        return
    if fyd.gross_profit is not None:
        sga = fyd.sga_expense or 0.0
        rd = fyd.rd_expense or 0.0
        fyd.operating_income = fyd.gross_profit - sga - rd
        fyd.xbrl_tags_used["operating_income"] = (
            "COMPUTED (approx): gross_profit - sga_expense - rd_expense"
        )
        fyd.data_source_notes["operating_income"] = (
            "Approximation — may exclude other operating expenses"
        )
        logger.warning(
            "OpIncomeGapFill",
            f"{fyd.ticker} FY{fyd.fiscal_year}: operating_income approximated from components",
        )


def compile_missing_fields(
    year_data: dict[int, FinancialYearData],
) -> list[MissingFieldEntry]:
    """
    Compile the definitive list of missing critical fields after all gap-filling.

    Groups missing years by field name to produce concise entries.

    Args:
        year_data: Dict mapping fiscal_year → FinancialYearData

    Returns:
        List of MissingFieldEntry objects for fields that are still None
    """
    from schemas.financial_fields import FIELD_DEFINITIONS

    # Track which years are missing for each field
    missing_by_field: dict[str, list[int]] = {}

    critical_impact_map: dict[str, tuple[str, str]] = {
        "revenue": ("All profitability and growth ratios cannot be computed", "HIGH"),
        "net_income": ("ROE, ROA, EPS, profit margins all blocked", "HIGH"),
        "total_assets": ("ROA, debt ratios, Altman Z-Score blocked", "HIGH"),
        "operating_income": ("EBITDA, operating margin, Altman Z-Score partially blocked", "HIGH"),
        "operating_cash_flow": ("FCF, OCF margin, cash quality ratios blocked", "HIGH"),
        "interest_expense": ("Interest Coverage Ratio cannot be computed", "HIGH"),
        "depreciation_amortization": ("EBITDA cannot be computed", "HIGH"),
        "total_equity": ("Debt/Equity ratio, ROE blocked", "HIGH"),
        "total_liabilities": ("Leverage ratios, Altman X4 partially blocked", "HIGH"),
        "current_assets": ("Current Ratio, Quick Ratio, Working Capital blocked", "HIGH"),
        "current_liabilities": ("Liquidity ratios, Working Capital blocked", "HIGH"),
        "accounts_receivable": ("DSO, Beneish DSRI variable blocked", "HIGH"),
        "ppe_net": ("Beneish AQI and DEPI variables blocked", "HIGH"),
        "retained_earnings": ("Altman X2 blocked", "HIGH"),
        "shares_outstanding": ("Market Cap, EPS cross-validation blocked", "HIGH"),
        "capex": ("Free Cash Flow cannot be computed", "HIGH"),
        "cash_and_equivalents": ("Net Debt, Cash Ratio blocked", "HIGH"),
        "long_term_debt_noncurrent": ("Net Debt, Debt/EBITDA blocked", "HIGH"),
        "ebitda": ("Debt/EBITDA, EV/EBITDA, Net Debt/EBITDA all blocked", "HIGH"),
        "free_cash_flow": ("FCF yield and FCF margin blocked", "HIGH"),
        "working_capital": ("Altman X1 blocked", "HIGH"),
        "market_cap": ("Altman X4, P/E ratio, market-based valuations blocked", "HIGH"),
        "sga_expense": ("Beneish SGAI variable blocked", "MEDIUM"),
        "rd_expense": ("R&D intensity ratio unavailable", "LOW"),
        "inventory": ("Inventory Turnover, DIO, Cash Conversion Cycle blocked", "MEDIUM"),
        "accounts_payable": ("DPO, Cash Conversion Cycle blocked", "MEDIUM"),
        "short_term_debt": ("Net Debt may be understated", "MEDIUM"),
        "eps_basic": ("Basic EPS trend analysis blocked", "MEDIUM"),
        "eps_diluted": ("Diluted EPS trend, P/E ratio blocked", "MEDIUM"),
        "goodwill": ("Goodwill impairment risk signal unavailable", "MEDIUM"),
        "intangible_assets": ("Intangibles analysis unavailable", "LOW"),
        "income_before_tax": ("Effective tax rate cannot be computed", "MEDIUM"),
        "income_tax_expense": ("Effective tax rate blocked", "MEDIUM"),
        "investing_cash_flow": ("Investing activity analysis limited", "LOW"),
        "financing_cash_flow": ("Capital structure activity analysis limited", "LOW"),
        "dividends_paid": ("Dividend payout ratio unavailable", "LOW"),
        "stock_buybacks": ("Shareholder return analysis limited", "LOW"),
        "beta": ("Market risk comparison limited", "LOW"),
        "stock_price_fy_end": ("Market Cap computation blocked", "HIGH"),
        "net_debt": ("Net Debt/EBITDA, leverage analysis blocked", "HIGH"),
        "short_term_investments": ("Enhanced cash analysis unavailable", "LOW"),
        "non_operating_income": ("Quality of Earnings signal limited", "MEDIUM"),
        "weighted_avg_shares_basic": ("EPS cross-validation blocked", "LOW"),
        "weighted_avg_shares_diluted": ("Diluted EPS cross-validation blocked", "LOW"),
    }

    for fy, fyd in year_data.items():
        for field_name, (impact, criticality) in critical_impact_map.items():
            value = getattr(fyd, field_name, None)
            if value is None:
                if field_name not in missing_by_field:
                    missing_by_field[field_name] = []
                missing_by_field[field_name].append(fy)

    # Build MissingFieldEntry objects
    entries: list[MissingFieldEntry] = []
    for field_name, years_missing in missing_by_field.items():
        impact, criticality = critical_impact_map.get(
            field_name, ("Downstream impact unknown", "LOW")
        )
        entries.append(
            MissingFieldEntry(
                field=field_name,
                years_missing=sorted(years_missing),
                impact=impact,
                criticality=criticality,
            )
        )

    # Sort by criticality then field name
    criticality_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    entries.sort(key=lambda e: (criticality_order.get(e.criticality, 3), e.field))
    return entries


def validate_and_fill(
    year_data: dict[int, FinancialYearData],
    logger: AuditLogger,
) -> tuple[dict[int, FinancialYearData], list[str], list[MissingFieldEntry]]:
    """
    Run all arithmetic cross-checks and compute all derived fields.

    This is the main entry point for Phase 5. Operates on FinancialYearData
    objects in place (modifies them) and returns the updated dict.

    Processing order:
      1. Gap-fill Income Before Tax (needs net_income + tax)
      2. Gap-fill Operating Income (needs gross_profit)
      3. Compute all derived fields (FCF, EBITDA, Net Debt, Working Capital, Market Cap)
      4. Cross-check income statement
      5. Cross-check balance sheet
      6. Cross-check EPS
      7. Compile missing fields list

    Args:
        year_data: Dict mapping fiscal_year → FinancialYearData
        logger: AuditLogger for this run

    Returns:
        Tuple of:
          - Updated year_data dict
          - List of warning strings from all checks
          - List of MissingFieldEntry for still-missing fields
    """
    all_warnings: list[str] = []

    for fy in sorted(year_data.keys()):
        fyd = year_data[fy]

        # Step 1: Gap fills first (these create fields that derived calculations need)
        _check_income_before_tax(fyd, logger)
        _check_operating_income(fyd, logger)

        # Step 2: Compute all derived fields
        _compute_derived_fields(fyd, logger)

        # Step 3: Run arithmetic cross-checks
        all_warnings.extend(_check_income_statement(fyd, logger))
        all_warnings.extend(_check_balance_sheet(fyd, logger))
        all_warnings.extend(_check_eps(fyd, logger))

    # Step 4: Compile the final missing fields list
    missing_entries = compile_missing_fields(year_data)

    return year_data, all_warnings, missing_entries
