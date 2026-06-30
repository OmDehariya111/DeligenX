"""
schemas/financial_fields.py — XBRL Tag Sequences and Field Definitions
Agent: Agent 1 (Ingestion Agent)
Reads: Nothing (static configuration)
Writes: Nothing (read-only config data)

This file is the single source of truth for which XBRL tags to try, in which
priority order, for each of the 45 financial fields collected by the Ingestion
Agent. It contains ONLY data — no business logic.

Structure per field:
    {
        "field_name": {
            "tags": ["PrimaryTag", "FallbackTag1", "FallbackTag2", ...],
            "taxonomy": "us-gaap" | "dei",
            "unit": "USD" | "USD/shares" | "shares",
            "sign_convention": "positive" | "normal",  # positive = always stored +
            "critical": True | False,   # True = blocks downstream calcs if missing
            "zero_if_absent": False,    # True = treat as zero (not error) if missing
            "notes": "Human readable note about this field"
        }
    }

Tags with "dei:" prefix come from facts["dei"] in the CompanyFacts JSON.
All other tags come from facts["us-gaap"].
"""

from typing import Any

# ── Complete XBRL tag sequences for all 45 financial fields ───────────────
FIELD_DEFINITIONS: dict[str, dict[str, Any]] = {

    # ══════════════════════════════════════════════════════════════════════
    # GROUP A — INCOME STATEMENT (13 fields)
    # ══════════════════════════════════════════════════════════════════════

    "revenue": {
        "tags": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",     # sum with SalesRevenueServicesNet if needed
            "SalesRevenueServicesNet",  # see special_handling below
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "special_handling": "sum_goods_services",  # sum tags[4]+tags[5] if tags[0-3] missing
        "notes": "Net revenue / net sales. Most critical income statement field.",
    },

    "cost_of_revenue": {
        "tags": [
            "CostOfGoodsAndServicesSold",
            "CostOfRevenue",
            "CostOfGoodsSold",
            "CostOfServices",
            "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "Cost of goods sold / cost of services. Required for gross margin.",
    },

    "gross_profit": {
        "tags": [
            "GrossProfit",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "computed_fallback": "revenue - cost_of_revenue",
        "notes": "Can be computed from Revenue - Cost_of_Revenue if tag absent.",
    },

    "sga_expense": {
        "tags": [
            "SellingGeneralAndAdministrativeExpense",
            "GeneralAndAdministrativeExpense",  # try combining with next tag
            "SellingAndMarketingExpense",
            "SellingExpense",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "special_handling": "sum_ga_sales",  # sum tags[1]+tags[2] if tag[0] missing
        "notes": "SG&A expense. Required for Beneish SGAI variable.",
    },

    "rd_expense": {
        "tags": [
            "ResearchAndDevelopmentExpense",
            "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": False,
        "zero_if_absent": True,  # Not applicable for many industries
        "notes": "R&D expense. Treated as zero if absent — not a data error.",
    },

    "operating_income": {
        "tags": [
            "OperatingIncomeLoss",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",  # can be negative (operating loss)
        "critical": True,
        "zero_if_absent": False,
        "computed_fallback": "gross_profit - sga_expense - rd_expense",
        "notes": "EBIT. Negative value is valid (operating loss).",
    },

    "interest_expense": {
        "tags": [
            "InterestExpenseNonoperating",         # Apple and many tech companies
            "InterestExpense",                      # Most companies; Apple & MSFT use thru FY2023
            "InterestAndDebtExpense",
            "InterestExpenseDebt",
            "InterestCostsIncurred",               # Apple alternative thru FY2023
            "InterestExpenseLongTermDebt",
            "InterestExpenseRelatedParty",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": (
            "Apple (AAPL) and Microsoft (MSFT) stopped XBRL-tagging interest_expense after FY2023. "
            "They net interest expense into Other Income/Expense. "
            "interest_expense = None for AAPL/MSFT FY2024+ is CORRECT — not a data error."
        ),
    },

    "income_before_tax": {
        "tags": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "computed_fallback": "net_income + income_tax_expense",
        "notes": "Income before income taxes. Can be computed if both components available.",
    },

    "income_tax_expense": {
        "tags": [
            "IncomeTaxExpenseBenefit",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",  # can be negative (tax benefit year)
        "critical": True,
        "zero_if_absent": False,
        "notes": "Negative value is valid — tax benefit in loss years.",
    },

    "net_income": {
        "tags": [
            "NetIncomeLoss",
            "NetIncomeLossAvailableToCommonStockholdersBasic",
            "ProfitLoss",
            "NetIncome",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",  # can be negative (net loss)
        "critical": True,
        "zero_if_absent": False,
        "notes": "Use tag[0] for ratios; use tag[1] for EPS cross-validation.",
    },

    "eps_basic": {
        "tags": [
            "EarningsPerShareBasic",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD/shares",  # CRITICAL: filter for this unit, not USD
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "EPS Basic. Unit is USD/shares — filter the units section correctly.",
    },

    "eps_diluted": {
        "tags": [
            "EarningsPerShareDiluted",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD/shares",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "EPS Diluted. Unit is USD/shares.",
    },

    "non_operating_income": {
        "tags": [],  # Always computed — no direct XBRL tag
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "computed_fallback": "income_before_tax - operating_income",
        "notes": "Non-operating income = Income Before Tax - Operating Income. "
                 "High non-op income masking weak operations is a QoE red flag.",
    },

    # ══════════════════════════════════════════════════════════════════════
    # GROUP B — BALANCE SHEET (19 fields)
    # ══════════════════════════════════════════════════════════════════════

    "total_assets": {
        "tags": [
            "Assets",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "Standard 'Assets' tag. Virtually universal.",
    },

    "current_assets": {
        "tags": [
            "AssetsCurrent",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "Required for liquidity ratios and Altman X1.",
    },

    "cash_and_equivalents": {
        "tags": [
            "CashAndCashEquivalentsAtCarryingValue",
            "Cash",
            "CashAndCashEquivalents",
            "CashCashEquivalentsAndShortTermInvestments",  # inflates if used — log warning
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "If tag[3] used, log that value includes short-term investments.",
    },

    "short_term_investments": {
        "tags": [
            "ShortTermInvestments",
            "MarketableSecuritiesCurrent",
            "AvailableForSaleSecuritiesCurrent",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": False,
        "zero_if_absent": True,  # Not all companies hold ST investments
        "notes": "Treat as zero if absent — not a data error.",
    },

    "accounts_receivable": {
        "tags": [
            "AccountsReceivableNetCurrent",
            "ReceivablesNetCurrent",
            "AccountsReceivableNet",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "Required for DSO and Beneish DSRI variable.",
    },

    "inventory": {
        "tags": [
            "InventoryNet",
            "Inventories",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": False,
        "zero_if_absent": True,  # Service/software companies carry zero inventory
        "notes": "Treat as zero if absent. Check SIC code — expected for retailers.",
    },

    "ppe_net": {
        "tags": [
            "PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization", # TSLA FY2025+
            "PropertyPlantAndEquipmentGross",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "Property, Plant, and Equipment Net. Falls back to Gross if Net isn't reported.",
    },

    "goodwill": {
        "tags": [
            "Goodwill",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": False,
        "zero_if_absent": True,  # Companies without acquisitions have zero goodwill
        "notes": "Treat as zero if absent. High % of assets = impairment risk.",
    },

    "intangible_assets": {
        "tags": [
            "FiniteLivedIntangibleAssetsNet",
            "IntangibleAssetsNetExcludingGoodwill",
            "IndefiniteLivedIntangibleAssetsExcludingGoodwill",  # sum with tag[0] if needed
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": False,
        "zero_if_absent": True,
        "special_handling": "sum_finite_indefinite",  # sum tags[0]+tags[2] if tag[1] absent
        "notes": "Sum finite + indefinite intangibles if total tag absent.",
    },

    "total_liabilities": {
        "tags": [
            "Liabilities",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "Required for D/A ratio and Altman X4.",
    },

    "current_liabilities": {
        "tags": [
            "LiabilitiesCurrent",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "Required for liquidity ratios, working capital, and Altman X1.",
    },

    "accounts_payable": {
        "tags": [
            "AccountsPayableCurrent",
            "AccountsPayable",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "notes": "Required for DPO and Cash Conversion Cycle.",
    },

    "short_term_debt": {
        "tags": [
            "ShortTermBorrowings",
            "LongTermDebtCurrent",    # current portion of LTD
            "CommercialPaper",
            "DebtCurrent",            # may be total — check vs sum of [0]+[1]
            "NotesPayableCurrent",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": True,  # Many companies have no short-term debt
        "special_handling": "sum_st_debt_components",
        "notes": "Sum ShortTermBorrowings + LongTermDebtCurrent if both found. "
                 "Check DebtCurrent is not double-counting.",
    },

    "long_term_debt_noncurrent": {
        "tags": [
            "LongTermDebtNoncurrent",  # preferred: explicitly excludes current portion
            "LongTermDebt",            # may include current portion — log if used
            "LongTermNotesPayable",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": True,
        "notes": "Use Noncurrent to avoid double-counting with short_term_debt.",
    },

    "total_equity": {
        "tags": [
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "PartnersCapital",
            "MembersEquity",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",  # can be negative
        "critical": True,
        "zero_if_absent": False,
        "notes": "For Apple, StockholdersEquity (excl. NCI) is used for D/E and ROE. "
                 "Balance sheet discrepancy > 5% with NCI equity is expected — logged as warning not error.",
    },

    "retained_earnings": {
        "tags": [
            "RetainedEarningsAccumulatedDeficit",
            "RetainedEarnings",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",  # can be negative (accumulated deficit)
        "critical": True,
        "zero_if_absent": False,
        "notes": "Required for Altman X2. Negative = accumulated deficit (valid).",
    },

    "shares_outstanding": {
        "tags": [
            "CommonStockSharesOutstanding",
            "EntityCommonStockSharesOutstanding",  # dei taxonomy — access via facts["dei"]
        ],
        "taxonomy": "us-gaap",  # tag[1] uses "dei" — handled specially in extractor
        "unit": "shares",
        "sign_convention": "positive",
        "critical": True,
        "zero_if_absent": False,
        "notes": "Tag[1] is in facts['dei'] not facts['us-gaap']. Unit = shares.",
    },

    "weighted_avg_shares_basic": {
        "tags": [
            "WeightedAverageNumberOfSharesOutstandingBasic",
        ],
        "taxonomy": "us-gaap",
        "unit": "shares",
        "sign_convention": "positive",
        "critical": True,
        "zero_if_absent": False,
        "notes": "For EPS cross-validation. Unit = shares.",
    },

    "weighted_avg_shares_diluted": {
        "tags": [
            "WeightedAverageNumberOfDilutedSharesOutstanding",
        ],
        "taxonomy": "us-gaap",
        "unit": "shares",
        "sign_convention": "positive",
        "critical": True,
        "zero_if_absent": False,
        "notes": "For EPS Diluted cross-validation. Unit = shares.",
    },

    # ══════════════════════════════════════════════════════════════════════
    # GROUP C — CASH FLOW STATEMENT (8 fields)
    # ══════════════════════════════════════════════════════════════════════

    "operating_cash_flow": {
        "tags": [
            "NetCashProvidedByUsedInOperatingActivities",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",  # can be negative
        "critical": True,
        "zero_if_absent": False,
        "notes": "Virtually universal tag. Negative valid for loss-making companies.",
    },

    "capex": {
        "tags": [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",  # Used by NVDA
            "CapitalExpenditureContinuingOperations",
            "PaymentsForCapitalImprovements",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",  # naturally an outflow (recorded as positive)
        "critical": True,
        "zero_if_absent": False,
        "notes": "Used directly in Free Cash Flow computation.",
    },

    "depreciation_amortization": {
        "tags": [
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "Depreciation",              # sum with AmortizationOfIntangibleAssets if needed
            "AmortizationOfIntangibleAssets",  # sum with Depreciation if needed
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "positive",  # add-back in cash flow = positive
        "critical": True,
        "zero_if_absent": False,
        "special_handling": "sum_dep_amort",  # sum tags[2]+tags[3] if tags[0,1] absent
        "notes": "Critical for EBITDA. Cash flow statement is most reliable source.",
    },

    "free_cash_flow": {
        "tags": [],  # Always computed
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "computed_fallback": "operating_cash_flow - capex",
        "notes": "Always computed for cross-company comparability. "
                 "FCF = Operating Cash Flow - CapEx.",
    },

    "investing_cash_flow": {
        "tags": [
            "NetCashProvidedByUsedInInvestingActivities",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",  # typically negative (cash used in investing)
        "critical": False,
        "zero_if_absent": False,
        "notes": "Usually negative. Context for M&A activity.",
    },

    "financing_cash_flow": {
        "tags": [
            "NetCashProvidedByUsedInFinancingActivities",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "normal",
        "critical": False,
        "zero_if_absent": False,
        "notes": "Context for debt issuance, buybacks, dividends.",
    },

    "dividends_paid": {
        "tags": [
            "PaymentsOfDividends",
            "PaymentsOfDividendsCommonStock",
            "PaymentsOfDividendsPreferredStockAndPreferenceStock",  # sum with tag[1]
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "positive",  # Stored as positive (cash paid out)
        "critical": False,
        "zero_if_absent": True,  # Growth companies pay no dividends — not an error
        "special_handling": "sum_common_preferred_dividends",
        "notes": "Many growth companies have no dividends. Missing = zero, not error.",
    },

    "stock_buybacks": {
        "tags": [
            "PaymentsForRepurchaseOfCommonStock",
            "TreasuryStockValueAcquiredCostMethod",
        ],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "sign_convention": "positive",  # Stored as positive (cash paid out)
        "critical": False,
        "zero_if_absent": True,
        "notes": "Stored as positive. Missing = no buybacks, not an error.",
    },

    # ══════════════════════════════════════════════════════════════════════
    # GROUP D — DERIVED METRICS (always computed, never from EDGAR directly)
    # ══════════════════════════════════════════════════════════════════════

    "ebitda": {
        "tags": [],  # Always computed
        "taxonomy": None,
        "unit": "USD",
        "sign_convention": "normal",
        "critical": True,
        "zero_if_absent": False,
        "computed_fallback": "operating_income + depreciation_amortization",
        "notes": "EBITDA = Operating Income + D&A. Missing if either component missing.",
    },

    "net_debt": {
        "tags": [],  # Always computed
        "taxonomy": None,
        "unit": "USD",
        "sign_convention": "normal",  # can be negative (net cash position)
        "critical": True,
        "zero_if_absent": False,
        "computed_fallback": "long_term_debt_noncurrent + short_term_debt - cash_and_equivalents",
        "notes": "Net Debt = LT Debt + ST Debt - Cash. Negative = net cash position.",
    },

    "working_capital": {
        "tags": [],  # Always computed
        "taxonomy": None,
        "unit": "USD",
        "sign_convention": "normal",  # can be negative
        "critical": True,
        "zero_if_absent": False,
        "computed_fallback": "current_assets - current_liabilities",
        "notes": "Working Capital = Current Assets - Current Liabilities. Altman X1 numerator.",
    },

    # ══════════════════════════════════════════════════════════════════════
    # GROUP E — MARKET DATA (from yfinance, not EDGAR)
    # ══════════════════════════════════════════════════════════════════════

    "stock_price_fy_end": {
        "tags": [],  # From yfinance historical data
        "taxonomy": "yfinance",
        "unit": "USD",
        "sign_convention": "positive",
        "critical": True,
        "zero_if_absent": False,
        "notes": "Closing price on fiscal year end date. Required for Market Cap.",
    },

    "beta": {
        "tags": [],  # From yfinance .info
        "taxonomy": "yfinance",
        "unit": "ratio",
        "sign_convention": "normal",
        "critical": False,
        "zero_if_absent": False,
        "notes": "5-year monthly beta vs S&P 500 from Yahoo Finance. Supplementary.",
    },

    "market_cap": {
        "tags": [],  # Always computed
        "taxonomy": None,
        "unit": "USD",
        "sign_convention": "positive",
        "critical": True,
        "zero_if_absent": False,
        "computed_fallback": "stock_price_fy_end * shares_outstanding",
        "notes": "Market Cap = Stock Price at FY End × Shares Outstanding. Altman X4 numerator.",
    },
}

# ── Convenience accessors ──────────────────────────────────────────────────

def get_tags_for_field(field_name: str) -> list[str]:
    """
    Return the ordered list of XBRL tags to try for a given field name.

    Args:
        field_name: Key from FIELD_DEFINITIONS

    Returns:
        List of tag strings in priority order (first = most preferred)

    Raises:
        KeyError: If field_name is not a recognized field
    """
    return FIELD_DEFINITIONS[field_name]["tags"]


def is_computed_field(field_name: str) -> bool:
    """
    Return True if this field is always computed (never fetched from EDGAR).

    Args:
        field_name: Key from FIELD_DEFINITIONS
    """
    defn = FIELD_DEFINITIONS[field_name]
    return len(defn["tags"]) == 0 and defn.get("taxonomy") not in ("yfinance",)


def is_market_data_field(field_name: str) -> bool:
    """
    Return True if this field comes from yfinance (not EDGAR).

    Args:
        field_name: Key from FIELD_DEFINITIONS
    """
    return FIELD_DEFINITIONS[field_name].get("taxonomy") == "yfinance"


def get_critical_fields() -> list[str]:
    """Return the list of field names marked as critical=True."""
    return [name for name, defn in FIELD_DEFINITIONS.items() if defn["critical"]]


def get_zero_if_absent_fields() -> list[str]:
    """Return field names that should be stored as zero (not NULL) when absent."""
    return [name for name, defn in FIELD_DEFINITIONS.items() if defn.get("zero_if_absent")]


# Ordered list of all 45 field names — matches financial_data table column order
ALL_FIELD_NAMES: list[str] = list(FIELD_DEFINITIONS.keys())

# Subset: fields that require extraction from CompanyFacts API
EDGAR_FIELDS: list[str] = [
    name for name, defn in FIELD_DEFINITIONS.items()
    if defn.get("taxonomy") in ("us-gaap", "dei") and defn["tags"]
]

# Subset: fields that are purely computed (no API call needed for extraction)
COMPUTED_FIELDS: list[str] = [
    name for name, defn in FIELD_DEFINITIONS.items()
    if not defn["tags"] and defn.get("taxonomy") not in ("yfinance",)
]

# 8-K event type taxonomy (26 items, includes 1.05 and 5.07)
# ESTABLISHED DECISION from F.5: 26-item taxonomy
EVENT_TYPE_MAP: dict[str, str] = {
    "1.01": "Material Definitive Agreement",
    "1.02": "Termination of Material Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.04": "Mine Safety — Reporting of Shutdowns and Patterns of Violations",
    "1.05": "Material Cybersecurity Incident",
    "2.01": "Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate or Increase Direct Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure or Appointment of Principal Officers, Directors",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.04": "Temporary Suspension of Trading Under Registrant Employee Benefit Plans",
    "5.05": "Amendments to the Registrant Code of Ethics",
    "5.06": "Change in Shell Company Status",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "5.08": "Shareholder Director Nominations",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

# 10-K sections to extract for ChromaDB (section_code → human label)
TEN_K_SECTIONS: dict[str, str] = {
    "item_1":      "Business Description",
    "item_1a":     "Risk Factors",
    "item_3":      "Legal Proceedings",
    "item_7":      "Management Discussion and Analysis",
    "item_7a":     "Quantitative and Qualitative Disclosures About Market Risk",
    "item_8_notes": "Notes to Financial Statements",
}

# 10-K sections to SKIP (not stored in ChromaDB)
TEN_K_SKIP_ITEMS: set[str] = {
    "item_2", "item_4", "item_5", "item_6",
    "item_9", "item_9a", "item_9b",
    "item_10", "item_11", "item_12", "item_13", "item_14", "item_15",
}
