"""
core/config.py — DeligenX Central Configuration
Agent: All agents (shared core module)
Reads: .env file
Writes: Nothing (read-only settings)

Single source of truth for all configuration: API keys, file paths, tunable
parameters, rate-limit constants, and model names. Every other module in the
project imports from here. No configuration is scattered across tool files.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """
    Pydantic V2 Settings model that loads from .env and environment variables.
    All fields have sensible defaults except secret keys, which are optional
    (agents that don't use them won't fail if they're absent).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Secret API Keys ───────────────────────────────────────────────────
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API key")
    NEWSAPI_KEY: str = Field(default="", description="NewsAPI.org key")
    FRED_API_KEY: str = Field(default="", description="FRED API key")

    # ── Storage Paths ─────────────────────────────────────────────────────
    DELIGENX_DB_PATH: str = Field(default="data/db/deligenx.db")
    DELIGENX_CHROMADB_PATH: str = Field(default="data/chromadb")
    DELIGENX_CACHE_PATH: str = Field(default="data/cache")
    DELIGENX_OUTPUTS_PATH: str = Field(default="data/outputs")
    DELIGENX_LOGS_PATH: str = Field(default="data/logs")

    # ── Cache ─────────────────────────────────────────────────────────────
    DELIGENX_CACHE_TTL_DAYS: int = Field(default=7, description="Cache validity in days")

    # ── ChromaDB ──────────────────────────────────────────────────────────
    CHROMADB_COLLECTION_NAME: str = Field(default="deligenx_filings")

    # ── Embedding model (sentence-transformers, runs offline) ─────────────
    EMBEDDING_MODEL_NAME: str = Field(default="all-MiniLM-L6-v2")
    EMBEDDING_BATCH_SIZE: int = Field(default=32)

    # ── Text chunking parameters ──────────────────────────────────────────
    CHUNK_TARGET_WORDS: int = Field(default=650, description="Target words per chunk")
    CHUNK_MIN_WORDS: int = Field(default=500)
    CHUNK_MAX_WORDS: int = Field(default=800)
    CHUNK_OVERLAP_WORDS: int = Field(default=100)

    # ── SEC EDGAR rate limiting ───────────────────────────────────────────
    EDGAR_MIN_SLEEP_SEC: float = Field(default=0.12, description="Min sleep between EDGAR calls")
    EDGAR_RETRY_BACKOFF: list[float] = Field(default=[1.0, 2.0, 4.0])
    EDGAR_MAX_RETRIES: int = Field(default=3)
    EDGAR_USER_AGENT: str = Field(
        default="DeligenX omdehariya111@gmail.com",
        description="SEC requires a User-Agent identifying the app and contact",
    )

    # ── Gemini rate limiting ──────────────────────────────────────────────
    GEMINI_POST_CALL_SLEEP_SEC: float = Field(
        default=4.0, description="Mandatory sleep after every Gemini call (free tier = 15 RPM)"
    )

    # ── FRED rate limiting ────────────────────────────────────────────────
    FRED_MIN_SLEEP_SEC: float = Field(default=0.5)

    # ── NewsAPI ───────────────────────────────────────────────────────────
    NEWSAPI_DAILY_LIMIT: int = Field(default=100)

    # ── LLM model names ───────────────────────────────────────────────────
    GEMINI_MODEL_PRIMARY: str = Field(default="gemini-2.0-flash")
    GEMINI_MODEL_VALIDATION: str = Field(default="gemini-2.0-flash-lite")

    # ── Financial data parameters ─────────────────────────────────────────
    FINANCIAL_YEARS_TO_COLLECT: int = Field(default=5, description="How many fiscal years back")
    FILINGS_10K_TO_PROCESS: int = Field(default=3, description="How many 10-K filings for text/VectorDB")
    FILINGS_8K_LOOKBACK_YEARS: int = Field(default=2, description="How many years of 8-K filings")

    # ── Arithmetic validation tolerances ─────────────────────────────────
    INCOME_STMT_TOLERANCE_PCT: float = Field(default=0.005, description="0.5% tolerance")
    BALANCE_SHEET_TOLERANCE_PCT: float = Field(default=0.005)
    EPS_VALIDATION_TOLERANCE_PCT: float = Field(default=0.05, description="5% tolerance")
    FCF_VALIDATION_TOLERANCE_PCT: float = Field(default=0.02, description="2% tolerance")

    # ── SEC EDGAR endpoints (base URLs, no trailing slash) ─────────────────
    EDGAR_TICKERS_URL: str = Field(default="https://www.sec.gov/files/company_tickers.json")
    EDGAR_SUBMISSIONS_BASE: str = Field(default="https://data.sec.gov/submissions")
    EDGAR_COMPANYFACTS_BASE: str = Field(default="https://data.sec.gov/api/xbrl/companyfacts")
    EDGAR_ARCHIVES_BASE: str = Field(default="https://www.sec.gov/Archives/edgar/data")

    def db_path(self) -> Path:
        """Absolute path to the SQLite database file."""
        return Path(self.DELIGENX_DB_PATH).resolve()

    def chromadb_path(self) -> Path:
        """Absolute path to the ChromaDB persistent store directory."""
        return Path(self.DELIGENX_CHROMADB_PATH).resolve()

    def cache_path(self) -> Path:
        """Absolute path to the local cache directory."""
        return Path(self.DELIGENX_CACHE_PATH).resolve()

    def outputs_path(self) -> Path:
        """Absolute path to the outputs directory."""
        return Path(self.DELIGENX_OUTPUTS_PATH).resolve()

    def logs_path(self) -> Path:
        """Absolute path to the structured logs directory."""
        return Path(self.DELIGENX_LOGS_PATH).resolve()

    def ticker_output_path(self, ticker: str) -> Path:
        """Absolute path to a specific ticker's output directory."""
        return self.outputs_path() / ticker.upper().strip()

    def ensure_directories(self) -> None:
        """Create all required directories if they do not already exist."""
        for p in [
            self.db_path().parent,
            self.chromadb_path(),
            self.cache_path(),
            self.outputs_path(),
            self.logs_path(),
        ]:
            p.mkdir(parents=True, exist_ok=True)


# Module-level singleton — import this everywhere:  from core.config import settings
settings = Settings()
