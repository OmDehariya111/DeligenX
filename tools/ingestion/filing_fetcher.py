"""
tools/ingestion/filing_fetcher.py — Phase 2: SEC Filing Download
Agent: Agent 1 (Ingestion Agent)
Reads: SEC EDGAR Archives (filing documents)
Writes: data/cache/{cik}/{accession_no_hyphens}/main_doc.htm

Constructs the correct EDGAR URLs for each filing and downloads the HTML document.

Domain intelligence (proven from production use):
  - The main document filename follows the pattern: {ticker_lower}-{YYYYMMDD}.htm
    where YYYYMMDD is derived from the report_date (for 10-K) or filing_date (for 8-K)
  - The URL structure is:
    https://www.sec.gov/Archives/edgar/data/{cik_no_padding}/{accession_no_hyphens}/{ticker}-{date}.htm
  - Accession number WITHOUT hyphens in the directory path
  - CIK WITHOUT leading zeros in the URL path
  - Example (AAPL FY2024):
    https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm
  - XHTML detection: checks for <?xml or xmlns= before choosing parser

Strategy: Try the direct filename URL first (fast, no index needed).
If that fails, fall back to fetching the index JSON for the exact filename.
"""

import re
import time
from pathlib import Path
from typing import Optional

from core.config import settings
from core.http_client import get
from core.logger import AuditLogger
from schemas.ingestion_schemas import FilingRecord


def _get_filing_cache_path(cik: str, accession_no_hyphens: str) -> Path:
    """
    Return the local cache path for a downloaded filing document.

    Args:
        cik: 10-digit padded CIK
        accession_no_hyphens: Accession number with no hyphens

    Returns:
        Path to the cached filing document
    """
    return settings.cache_path() / cik / accession_no_hyphens / "main_doc.htm"


def _build_direct_url(
    cik: str,
    accession_no_hyphens: str,
    ticker: str,
    date_str: str,
) -> str:
    """
    Build the direct EDGAR document URL using the standard filename pattern.

    EDGAR names the main document of a filing as:
        {ticker_lowercase}-{YYYYMMDD}.htm
    where the date is the report_date (fiscal year end for 10-K) or filing_date (for 8-K).

    Example (AAPL FY2024 10-K):
        https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm

    Args:
        cik: 10-digit padded CIK (leading zeros stripped for URL)
        accession_no_hyphens: Accession number without hyphens
        ticker: Company ticker (will be lowercased)
        date_str: Date string in YYYY-MM-DD format

    Returns:
        Full EDGAR Archives document URL
    """
    cik_no_padding = str(int(cik))
    ticker_lower = ticker.lower().strip()
    date_nodash = date_str.replace("-", "")
    filename = f"{ticker_lower}-{date_nodash}.htm"
    return (
        f"{settings.EDGAR_ARCHIVES_BASE}/{cik_no_padding}/"
        f"{accession_no_hyphens}/{filename}"
    )


def _build_index_url(cik: str, accession_no_hyphens: str) -> str:
    """
    Build the EDGAR filing HTML index page URL.

    The index page at {accession_hyphens}-index.htm always exists and lists
    all documents in a filing. Reliable for both self-filed and third-party-filed
    documents.

    Example:
        https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/
                0000320193-24-000123-index.htm

    Args:
        cik: 10-digit padded CIK
        accession_no_hyphens: Accession number without hyphens (18 chars)

    Returns:
        URL for the filing HTML index page
    """
    cik_no_padding = str(int(cik))
    acc = accession_no_hyphens
    accession_hyphens = f"{acc[:10]}-{acc[10:12]}-{acc[12:]}"
    return (
        f"{settings.EDGAR_ARCHIVES_BASE}/{cik_no_padding}/"
        f"{accession_no_hyphens}/{accession_hyphens}-index.htm"
    )


def _find_main_doc_from_index_html(index_html: str, form_type: str) -> Optional[str]:
    """
    Parse the EDGAR filing HTML index page to find the main document filename.

    The index page is a table listing all files in the package with columns:
    Seq | Description | Document | Type | Size

    We look for the row where the Type column matches form_type and the Document
    column is a .htm file. Falls back to the largest non-exhibit .htm file.

    Args:
        index_html: HTML text of the index page
        form_type: Expected form type ('10-K', '8-K', etc.)

    Returns:
        Main document filename (e.g. 'aapl-20240928.htm') or None
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(index_html, "lxml")
    rows = soup.find_all("tr")

    candidates = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        # Find the document link and type cells
        link_tag = row.find("a", href=True)
        if not link_tag:
            continue

        href = link_tag.get("href", "")
        filename = href.split("/")[-1]

        if not filename.lower().endswith(".htm"):
            continue

        # Get the Type cell text (usually the last TD or near-last)
        type_text = ""
        for cell in reversed(cells):
            text = cell.get_text(strip=True).upper()
            if text in ("10-K", "10-K/A", "8-K", "8-K/A", "DEF 14A", "20-F"):
                type_text = text
                break

        # Skip exhibits
        skip = ("ex", "exhibit", "ex-", "xbrl", "index", "r1.", "r2.", "r3.")
        if any(s in filename.lower() for s in skip):
            continue

        score = 0
        if type_text == form_type.upper():
            score += 10  # Exact type match
        if not filename.lower().startswith("ex"):
            score += 1

        candidates.append((score, filename))

    if not candidates:
        # Last resort: grab any .htm link that isn't an index file
        all_links = re.findall(r'href="([^"]+\.htm[l]?)"', index_html, re.IGNORECASE)
        for link in all_links:
            fn = link.split("/")[-1].lower()
            if "index" not in fn and not fn.startswith("ex"):
                return link.split("/")[-1]
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def download_filing(
    filing: FilingRecord,
    cik: str,
    logger: AuditLogger,
    ticker: str = "",
) -> Optional[bytes]:
    """
    Download the main document of an SEC filing and return its raw bytes.

    Strategy (proven from production):
      1. Check local cache — if cached, return immediately (no network call)
      2. Try the DIRECT URL using the standard EDGAR filename pattern:
           {ticker_lower}-{YYYYMMDD}.htm  using report_date for 10-K, filing_date for 8-K
      3. If direct URL fails, fall back to the index JSON to find the exact filename
      4. Cache and return

    This matches the proven working approach from the original project.

    Args:
        filing: FilingRecord with accession number, form type, and dates
        cik: 10-digit padded CIK
        logger: AuditLogger for this run
        ticker: Company ticker (used to build direct URL filename)

    Returns:
        Raw bytes of the main filing document, or None if download failed
    """
    accession_no_hyphens = filing.accession_no_hyphens

    # ── Step 1: Check cache ────────────────────────────────────────────────
    cache_path = _get_filing_cache_path(cik, accession_no_hyphens)
    if cache_path.exists():
        logger.success(
            "DownloadFiling",
            f"{filing.form_type} {filing.filing_date} — loaded from cache",
        )
        return cache_path.read_bytes()

    t0 = time.monotonic()

    # ── Step 2: Try DIRECT URL first (fast path, no index needed) ─────────
    # Use report_date for 10-K (fiscal year end date), filing_date for 8-K
    date_for_url = filing.report_date if filing.report_date else filing.filing_date

    if ticker and date_for_url:
        direct_url = _build_direct_url(cik, accession_no_hyphens, ticker, date_for_url)
        response = get(direct_url, is_edgar=True)

        if response is not None and response.status_code == 200 and len(response.content) > 1000:
            raw_bytes = response.content
            _save_to_cache(cache_path, raw_bytes, logger)
            logger.success(
                "DownloadFiling",
                f"{filing.form_type} {filing.filing_date} — {len(raw_bytes):,} bytes (direct URL)",
                logger.elapsed_ms(t0),
            )
            return raw_bytes

        logger.warning(
            "DownloadFiling",
            f"Direct URL failed for {filing.form_type} {filing.filing_date}, "
            f"trying index JSON: {direct_url}",
        )

    # ── Step 3: Fallback — fetch HTML index page to find exact filename ──────
    index_url = _build_index_url(cik, accession_no_hyphens)
    index_response = get(index_url, is_edgar=True)

    if index_response is None or index_response.status_code != 200:
        logger.error(
            "DownloadFiling",
            f"Both direct URL and index page failed for "
            f"{filing.form_type} {filing.filing_date} "
            f"(accession: {filing.accession_number})",
            logger.elapsed_ms(t0),
        )
        return None

    main_doc_name = _find_main_doc_from_index_html(index_response.text, filing.form_type)
    if main_doc_name is None:
        logger.warning(
            "DownloadFiling",
            f"Could not identify main document in index page for "
            f"{filing.form_type} {filing.filing_date}",
        )
        return None

    cik_no_padding = str(int(cik))
    doc_url = (
        f"{settings.EDGAR_ARCHIVES_BASE}/{cik_no_padding}/"
        f"{accession_no_hyphens}/{main_doc_name}"
    )

    doc_response = get(doc_url, is_edgar=True)
    duration_ms = logger.elapsed_ms(t0)

    if doc_response is None:
        logger.error(
            "DownloadFiling",
            f"Failed to download {filing.form_type} {filing.filing_date}: {doc_url}",
            duration_ms,
        )
        return None

    raw_bytes = doc_response.content
    _save_to_cache(cache_path, raw_bytes, logger)

    logger.success(
        "DownloadFiling",
        f"{filing.form_type} {filing.filing_date} — {len(raw_bytes):,} bytes (index fallback)",
        duration_ms,
    )
    return raw_bytes


def _save_to_cache(cache_path: Path, raw_bytes: bytes, logger: AuditLogger) -> None:
    """
    Save downloaded filing bytes to the local cache.

    Args:
        cache_path: Target path to save the file
        raw_bytes: Raw bytes to save
        logger: AuditLogger for this run
    """
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw_bytes)
    except OSError as e:
        logger.warning("DownloadFiling", f"Cache write failed: {e}")


def detect_xhtml(raw_text: str) -> bool:
    """
    Detect whether an HTML document is actually XHTML (XML-based HTML).

    Domain intelligence (Block G.3): Check for <?xml or xmlns= in the response
    text. If found, use lxml-xml parser. Otherwise use lxml HTML parser.
    This eliminates XMLParsedAsHTMLWarning entirely.

    Args:
        raw_text: First few KB of the document text

    Returns:
        True if the document is XHTML, False if standard HTML
    """
    sample = raw_text[:2000]  # Only need to check the beginning
    return "<?xml" in sample or "xmlns=" in sample
