"""
schemas/ingestion_schemas.py — DeligenX Ingestion Agent Pydantic V2 Models
Agent: Agent 1 (Ingestion Agent) — models are also read by downstream agents
Reads: Nothing (schema definitions only)
Writes: Nothing (pure validation models)

All cross-boundary data that moves between tools, between an agent and the
database, or between agents passes through one of these Pydantic V2 models.
This catches schema mismatches before they silently produce wrong numbers.

Model dependency order (lower models do not import higher ones):
    ChunkMetadata         ← validated before every ChromaDB add()
    FilingRecord          ← one SEC filing entry from Submissions API
    CompanyIdentity       ← company metadata from Submissions API
    FinancialYearData     ← all 45 fields for one fiscal year
    XbrlTagsUsed          ← tag audit trail per field
    MissingFieldEntry     ← structured missing-field log entry
    VectorDbStats         ← ChromaDB chunk counts by source
    IngestionSummary      ← complete Contract 1 output (Block B)
"""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ══════════════════════════════════════════════════════════════════════════
# ChromaDB metadata — validated before every collection.add() call
# ══════════════════════════════════════════════════════════════════════════

class ChunkMetadata(BaseModel):
    """
    The 7 required metadata fields that EVERY chunk stored in ChromaDB must have.
    Block B Contract 6 defines these as mandatory. No exceptions.
    Validated via Pydantic before every ChromaDB add() operation.
    """

    ticker: str = Field(..., description="Company ticker, uppercase")
    filing_type: str = Field(
        ...,
        description="'10-K' | '8-K' | 'USER_FILE'",
        pattern=r"^(10-K|8-K|USER_FILE)$",
    )
    section_code: str = Field(
        ...,
        description="Section identifier: item_1, item_1a, item_3, item_7, "
                    "item_7a, item_8_notes, 8k_body, user_file",
    )
    fiscal_year: int = Field(
        ...,
        description="Calendar year the FY ends. Use 0 for 8-K and USER_FILE.",
        ge=0,
    )
    filing_date: str = Field(..., description="ISO date string e.g. '2024-11-01'")
    priority: str = Field(
        ...,
        description="'STANDARD' | 'HIGH' (user file chunks get HIGH)",
        pattern=r"^(STANDARD|HIGH)$",
    )
    event_type: str = Field(
        ...,
        description="For 8-K: the primary SEC item code e.g. '5.02'. "
                    "Empty string '' for all others.",
    )

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, v: str) -> str:
        """Ensure ticker is always uppercase and stripped."""
        return v.upper().strip()

    @field_validator("filing_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """Validate ISO date format YYYY-MM-DD."""
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"filing_date must be YYYY-MM-DD format, got: {v!r}") from exc
        return v

    def to_chromadb_dict(self) -> dict[str, Any]:
        """
        Return a plain dict suitable for ChromaDB metadata storage.
        ChromaDB requires metadata values to be str, int, float, or bool.
        """
        return {
            "ticker": self.ticker,
            "filing_type": self.filing_type,
            "section_code": self.section_code,
            "fiscal_year": self.fiscal_year,
            "filing_date": self.filing_date,
            "priority": self.priority,
            "event_type": self.event_type,
        }


# ══════════════════════════════════════════════════════════════════════════
# Filing records from Submissions API
# ══════════════════════════════════════════════════════════════════════════

class FilingRecord(BaseModel):
    """
    A single SEC filing entry extracted from the Submissions API response.
    Used to track which filings to download for text processing.
    """

    accession_number: str = Field(
        ...,
        description="Accession number with hyphens: 0000320193-24-000123",
    )
    form_type: str = Field(..., description="'10-K' | '8-K' | etc.")
    filing_date: str = Field(..., description="ISO date string: '2024-11-01'")
    report_date: Optional[str] = Field(
        None,
        description="Period of report date (fiscal year end date for 10-K)",
    )

    @property
    def accession_no_hyphens(self) -> str:
        """
        Accession number with hyphens removed.
        Required for EDGAR Archives directory URL construction.
        Correct: 000032019324000123
        Wrong (gives 404): 0000320193-24-000123 in the directory path.
        """
        return self.accession_number.replace("-", "")

    @property
    def accession_last_six(self) -> str:
        """Last 6 digits of the accession number, used in 8-K chunk IDs."""
        clean = self.accession_no_hyphens
        return clean[-6:]


# ══════════════════════════════════════════════════════════════════════════
# Company identity from Submissions API
# ══════════════════════════════════════════════════════════════════════════

class CompanyIdentity(BaseModel):
    """
    Company metadata extracted from the SEC Submissions API.
    Validated on first resolution and cached to disk.
    """

    ticker: str = Field(..., description="Uppercase ticker symbol")
    company_name: str = Field(..., description="Official SEC registered name")
    cik: str = Field(..., description="10-digit zero-padded CIK")
    sic_code: str = Field(..., description="4-digit SIC code")
    industry_name: str = Field(..., description="Human-readable SIC description")
    exchange: str = Field(default="", description="Stock exchange: Nasdaq / NYSE / etc.")
    state_of_incorp: str = Field(default="", description="State of incorporation code")
    fiscal_year_end: str = Field(
        ...,
        description="Raw MMDD string from SEC e.g. '0926' for Apple Sept 26",
    )
    fiscal_year_end_month: int = Field(
        ...,
        description="Integer month 1-12 extracted from fiscal_year_end",
        ge=1,
        le=12,
    )

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, v: str) -> str:
        """Ensure ticker is always uppercase and stripped."""
        return v.upper().strip()

    @field_validator("cik")
    @classmethod
    def validate_cik_format(cls, v: str) -> str:
        """Ensure CIK is 10 digits, zero-padded."""
        return str(v).zfill(10)

    @field_validator("fiscal_year_end_month")
    @classmethod
    def validate_month(cls, v: int) -> int:
        """Fiscal year end month must be 1–12."""
        if not 1 <= v <= 12:
            raise ValueError(f"fiscal_year_end_month must be 1–12, got {v}")
        return v


# ══════════════════════════════════════════════════════════════════════════
# Financial data for a single fiscal year
# ══════════════════════════════════════════════════════════════════════════

class FinancialYearData(BaseModel):
    """
    All 45 financial fields for a single fiscal year.
    All financial fields are Optional[float] — None means data is unavailable.
    Never store zero as a substitute for None (Iron Law 3).
    """

    ticker: str
    cik: str
    company_name: str
    fiscal_year: int
    fiscal_year_end_date: Optional[str] = None  # ISO date
    form_type: str = "10-K"

    # GROUP A — Income Statement
    revenue: Optional[float] = None
    cost_of_revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    sga_expense: Optional[float] = None
    rd_expense: Optional[float] = None
    operating_income: Optional[float] = None
    interest_expense: Optional[float] = None
    income_before_tax: Optional[float] = None
    income_tax_expense: Optional[float] = None
    net_income: Optional[float] = None
    eps_basic: Optional[float] = None
    eps_diluted: Optional[float] = None
    non_operating_income: Optional[float] = None

    # GROUP B — Balance Sheet
    total_assets: Optional[float] = None
    current_assets: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    short_term_investments: Optional[float] = None
    accounts_receivable: Optional[float] = None
    inventory: Optional[float] = None
    ppe_net: Optional[float] = None
    goodwill: Optional[float] = None
    intangible_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    current_liabilities: Optional[float] = None
    accounts_payable: Optional[float] = None
    short_term_debt: Optional[float] = None
    long_term_debt_noncurrent: Optional[float] = None
    total_equity: Optional[float] = None
    retained_earnings: Optional[float] = None
    shares_outstanding: Optional[float] = None
    weighted_avg_shares_basic: Optional[float] = None
    weighted_avg_shares_diluted: Optional[float] = None

    # GROUP C — Cash Flow Statement
    operating_cash_flow: Optional[float] = None
    capex: Optional[float] = None
    depreciation_amortization: Optional[float] = None
    free_cash_flow: Optional[float] = None
    investing_cash_flow: Optional[float] = None
    financing_cash_flow: Optional[float] = None
    dividends_paid: Optional[float] = None
    stock_buybacks: Optional[float] = None

    # GROUP D — Derived
    ebitda: Optional[float] = None
    net_debt: Optional[float] = None
    working_capital: Optional[float] = None

    # GROUP E — Market Data
    stock_price_fy_end: Optional[float] = None
    beta: Optional[float] = None
    market_cap: Optional[float] = None

    # Audit
    xbrl_tags_used: dict[str, str] = Field(default_factory=dict)
    data_source_notes: dict[str, str] = Field(default_factory=dict)
    ingestion_timestamp: str = ""

    def count_populated(self) -> int:
        """Return the count of financial fields (Groups A-E) that are not None."""
        financial_fields = [
            "revenue", "cost_of_revenue", "gross_profit", "sga_expense",
            "rd_expense", "operating_income", "interest_expense",
            "income_before_tax", "income_tax_expense", "net_income",
            "eps_basic", "eps_diluted", "non_operating_income",
            "total_assets", "current_assets", "cash_and_equivalents",
            "short_term_investments", "accounts_receivable", "inventory",
            "ppe_net", "goodwill", "intangible_assets", "total_liabilities",
            "current_liabilities", "accounts_payable", "short_term_debt",
            "long_term_debt_noncurrent", "total_equity", "retained_earnings",
            "shares_outstanding", "weighted_avg_shares_basic",
            "weighted_avg_shares_diluted", "operating_cash_flow", "capex",
            "depreciation_amortization", "free_cash_flow", "investing_cash_flow",
            "financing_cash_flow", "dividends_paid", "stock_buybacks",
            "ebitda", "net_debt", "working_capital",
            "stock_price_fy_end", "beta", "market_cap",
        ]
        return sum(1 for f in financial_fields if getattr(self, f) is not None)

    def to_db_dict(self) -> dict[str, Any]:
        """
        Return a dict suitable for SQLAlchemy Core INSERT/UPDATE.
        Serializes audit dicts to JSON strings.
        """
        import json
        d = self.model_dump()
        d["xbrl_tags_used"] = json.dumps(d.get("xbrl_tags_used", {}))
        d["data_source_notes"] = json.dumps(d.get("data_source_notes", {}))
        return d


# ══════════════════════════════════════════════════════════════════════════
# Audit trail models
# ══════════════════════════════════════════════════════════════════════════

class MissingFieldEntry(BaseModel):
    """
    A structured entry describing one missing financial field.
    Compiled after validation and attached to the Ingestion Summary.
    """

    field: str = Field(..., description="Financial field name e.g. 'interest_expense'")
    years_missing: list[int] = Field(..., description="Fiscal years where data is absent")
    impact: str = Field(..., description="Which downstream calculations are blocked")
    criticality: str = Field(
        ...,
        description="'HIGH' | 'MEDIUM' | 'LOW'",
        pattern=r"^(HIGH|MEDIUM|LOW)$",
    )


class VectorDbStats(BaseModel):
    """Statistics about chunks stored in ChromaDB, reported in Ingestion Summary."""

    total_chunks: int = Field(0, ge=0)
    chunks_10k: int = Field(0, ge=0)
    chunks_8k: int = Field(0, ge=0)
    chunks_user_file: int = Field(0, ge=0)
    filings_processed_10k: list[str] = Field(
        default_factory=list,
        description="Filing dates of 10-K filings processed",
    )
    filings_processed_8k: int = Field(0, description="Count of 8-K filings processed")


# ══════════════════════════════════════════════════════════════════════════
# Contract 1 — Ingestion Summary Report (Block B)
# ══════════════════════════════════════════════════════════════════════════

class IngestionSummary(BaseModel):
    """
    The complete Ingestion Summary Report (Block B Contract 1).
    Produced by Agent 1 and consumed by ALL downstream agents on startup.
    Path: data/outputs/{ticker}/ingestion_summary.json

    All required fields are listed here. All downstream agents validate this
    model on load to ensure they receive a structurally complete summary.
    """

    # Company identity
    ticker: str
    company_name: str
    cik: str = Field(..., description="10-digit zero-padded CIK")
    sic_code: str
    industry_name: str
    exchange: str
    fiscal_year_end_month: int = Field(..., ge=1, le=12)

    # Financial data coverage
    years_covered: list[int] = Field(..., description="Fiscal years with data collected")
    years_missing: list[int] = Field(
        default_factory=list,
        description="Requested years with no data found",
    )

    # Field coverage summary
    fields_with_data: int = Field(..., ge=0, description="Count of fields populated")
    fields_missing: int = Field(..., ge=0, description="Count of fields entirely absent")
    missing_critical_fields: list[MissingFieldEntry] = Field(default_factory=list)

    # Vector DB statistics
    vector_db_stats: VectorDbStats = Field(default_factory=VectorDbStats)

    # XBRL audit trail (field_name → tag used or formula string)
    xbrl_tags_used: dict[str, str] = Field(
        default_factory=dict,
        description="Maps each field name to the XBRL tag used to source it",
    )

    # Warnings and errors
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues: fallback tag used, partial data, etc.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Failed operations: download fails, timeouts, etc.",
    )

    # Run status
    run_status: Optional[str] = Field(
        ...,
        description="'SUCCESS' if no errors AND ≥3 10-K filings found. "
                    "'PARTIAL' if either condition fails. "
                    "None only on critical halt (ticker not found or API unreachable).",
    )

    # Timing
    ingestion_timestamp: str = Field(..., description="ISO 8601 UTC timestamp")
    ingestion_duration_sec: int = Field(..., ge=0)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, v: str) -> str:
        """Ensure ticker is always uppercase and stripped."""
        return v.upper().strip()

    @field_validator("cik")
    @classmethod
    def validate_cik(cls, v: str) -> str:
        """Ensure CIK is 10 digits, zero-padded."""
        return str(v).zfill(10)

    @field_validator("run_status")
    @classmethod
    def validate_run_status(cls, v: Optional[str]) -> Optional[str]:
        """run_status must be SUCCESS, PARTIAL, or None."""
        if v is not None and v not in ("SUCCESS", "PARTIAL"):
            raise ValueError(f"run_status must be 'SUCCESS', 'PARTIAL', or None. Got: {v!r}")
        return v
