"""
tests/fixtures.py — DeligenX Test Fixtures
Agent: Agent 1 (Ingestion Agent)

All company-specific hardcoded expected values live here.
Test files import from this module — never hardcode values in test functions.

This is the single source of truth for expected test values.
If any of these values change (unlikely — CIKs never change, SIC rarely changes),
update them here and all tests automatically pick up the change.
"""

# ── Apple Inc. (AAPL) — Primary test company ─────────────────────────────
# These values are authoritative facts from SEC EDGAR, verifiable at:
# https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193

AAPL_TICKER = "AAPL"
AAPL_CIK = "0000320193"                          # 10-digit zero-padded CIK
AAPL_COMPANY_NAME_CONTAINS = "Apple"             # Official name contains "Apple"
AAPL_SIC_CODE = "3571"                           # Electronic Computers
AAPL_INDUSTRY_CONTAINS = "Computer"              # Industry name contains "Computer"
AAPL_EXCHANGE = "Nasdaq"
AAPL_FY_END_MONTH = 9                            # September
AAPL_FY_END_RAW = "0926"                         # MMDD from SEC Submissions API
AAPL_MIN_YEARS_WITH_DATA = 4                     # Must have at least 4 fiscal years
AAPL_EXPECTED_RECENT_YEAR = 2024                 # Most recent fiscal year expected

# Revenue for FY2024 (Apple's Q4 2024 annual report)
# Approximate expected range: $380B–$400B (in full USD)
AAPL_REVENUE_FY2024_MIN = 380_000_000_000
AAPL_REVENUE_FY2024_MAX = 400_000_000_000

# Net income for FY2024 — approximate range: $90B–$110B
AAPL_NET_INCOME_FY2024_MIN = 90_000_000_000
AAPL_NET_INCOME_FY2024_MAX = 110_000_000_000

# ── Microsoft Corporation (MSFT) — Secondary test company ────────────────
MSFT_TICKER = "MSFT"
MSFT_CIK = "0000789019"
MSFT_FY_END_MONTH = 6  # June

# ── Tesla, Inc. (TSLA) ───────────────────────────────────────────────────
TSLA_TICKER = "TSLA"
TSLA_CIK = "0001318605"

# ── ChromaDB expected values ──────────────────────────────────────────────
# After a full AAPL run, these are the minimum expected chunk counts
AAPL_MIN_TOTAL_CHUNKS = 100          # Conservative minimum for partial run
AAPL_MIN_10K_CHUNKS = 50            # Minimum 10-K chunks from 1 filing
CHROMADB_REQUIRED_METADATA_FIELDS = [
    "ticker", "filing_type", "section_code", "fiscal_year",
    "filing_date", "priority", "event_type",
]

# ── SQLite expected values ─────────────────────────────────────────────────
FINANCIAL_DATA_TABLE_NAME = "financial_data"
FINANCIAL_DATA_REQUIRED_COLUMNS = [
    "id", "ticker", "cik", "company_name", "fiscal_year", "fiscal_year_end_date",
    "form_type",
    # GROUP A
    "revenue", "cost_of_revenue", "gross_profit", "sga_expense", "rd_expense",
    "operating_income", "interest_expense", "income_before_tax", "income_tax_expense",
    "net_income", "eps_basic", "eps_diluted", "non_operating_income",
    # GROUP B
    "total_assets", "current_assets", "cash_and_equivalents", "short_term_investments",
    "accounts_receivable", "inventory", "ppe_net", "goodwill", "intangible_assets",
    "total_liabilities", "current_liabilities", "accounts_payable", "short_term_debt",
    "long_term_debt_noncurrent", "total_equity", "retained_earnings",
    "shares_outstanding", "weighted_avg_shares_basic", "weighted_avg_shares_diluted",
    # GROUP C
    "operating_cash_flow", "capex", "depreciation_amortization", "free_cash_flow",
    "investing_cash_flow", "financing_cash_flow", "dividends_paid", "stock_buybacks",
    # GROUP D
    "ebitda", "net_debt", "working_capital",
    # GROUP E
    "stock_price_fy_end", "beta", "market_cap",
    # Audit
    "xbrl_tags_used", "data_source_notes", "ingestion_timestamp",
]
FINANCIAL_DATA_COLUMN_COUNT = len(FINANCIAL_DATA_REQUIRED_COLUMNS)  # 52

# ── Ingestion Summary expected values ─────────────────────────────────────
INGESTION_SUMMARY_REQUIRED_FIELDS = [
    "ticker", "company_name", "cik", "sic_code", "industry_name", "exchange",
    "fiscal_year_end_month", "years_covered", "years_missing",
    "fields_with_data", "fields_missing", "missing_critical_fields",
    "vector_db_stats", "xbrl_tags_used", "warnings", "errors",
    "run_status", "ingestion_timestamp", "ingestion_duration_sec",
]

VALID_RUN_STATUSES = {"SUCCESS", "PARTIAL", None}

# ── XBRL tag audit trail: these fields MUST have entries in xbrl_tags_used ──
XBRL_TAGS_REQUIRED_FIELDS = ["revenue", "net_income"]

# ── 8-K section code expected value ──────────────────────────────────────
EIGHT_K_SECTION_CODE = "8k_body"
EIGHT_K_FISCAL_YEAR = 0  # Contract 6: 8-K uses 0

# ── 10-K section codes ───────────────────────────────────────────────────
VALID_10K_SECTION_CODES = {
    "item_1", "item_1a", "item_3", "item_7", "item_7a", "item_8_notes"
}

# ── Priority values ───────────────────────────────────────────────────────
VALID_PRIORITY_VALUES = {"STANDARD", "HIGH"}

# ── Filing type values ────────────────────────────────────────────────────
VALID_FILING_TYPES = {"10-K", "8-K", "USER_FILE"}

# ── Cache TTL ─────────────────────────────────────────────────────────────
CACHE_HIT_MAX_SECONDS = 5  # Block G.4: force_refresh=False must complete in <5s
