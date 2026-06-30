"""
tools/ingestion/text_processor.py — Phase 2: Text Extraction, Chunking, Embedding, Storage
Agent: Agent 1 (Ingestion Agent)
Reads: Raw HTML bytes of 10-K and 8-K filing documents
Writes: ChromaDB vector store (data/chromadb/)

Responsibilities:
  1. Parse 10-K HTML: extract Items 1, 1A, 3, 7, 7A, 8 Notes
  2. Parse 8-K HTML: classify event type, extract full body text
  3. Clean extracted text (remove HTML artifacts, normalize whitespace)
  4. Chunk text: 500–800 words with 100-word overlap
  5. Generate embeddings using sentence-transformers (offline, all-MiniLM-L6-v2)
  6. Validate ChunkMetadata (Pydantic) before every ChromaDB add() call
  7. Store chunks in ChromaDB with 7-field metadata

Chunk ID formats:
  10-K: {ticker}_{year}_10k_{section_code}_chunk_{idx:03d}
  8-K:  {ticker}_8k_{date}_{accession_last6}_chunk_{idx:03d}
"""

import re
import time
import unicodedata
from typing import Optional

from bs4 import BeautifulSoup, Tag

from core.chromadb_client import get_collection
from core.config import settings
from core.logger import AuditLogger
from schemas.financial_fields import EVENT_TYPE_MAP, TEN_K_SECTIONS, TEN_K_SKIP_ITEMS
from schemas.ingestion_schemas import ChunkMetadata, FilingRecord


# ── Lazy-load sentence-transformers model (loaded once, reused) ────────────
_embedding_model = None


def _get_embedding_model():
    """
    Lazily load the sentence-transformers embedding model.

    TRANSFORMERS_OFFLINE=1 must be set to prevent network calls.
    The model is loaded once and cached in the module-level _embedding_model.

    Returns:
        SentenceTransformer model instance
    """
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
    return _embedding_model


def _parse_html(raw_bytes: bytes, is_xhtml: bool) -> BeautifulSoup:
    """
    Parse raw HTML bytes using the appropriate parser.

    Uses lxml-xml for XHTML (detected by filing_fetcher.detect_xhtml),
    and lxml for standard HTML. This eliminates XMLParsedAsHTMLWarning.

    Args:
        raw_bytes: Raw bytes of the filing document
        is_xhtml: True if the document is XHTML, False for standard HTML

    Returns:
        BeautifulSoup parse tree
    """
    parser = "lxml-xml" if is_xhtml else "lxml"
    return BeautifulSoup(raw_bytes, parser)


def _clean_text(raw_text: str) -> str:
    """
    Clean raw extracted text from HTML parsing.

    Removes:
      - Residual HTML tags (if any slipped through BS4)
      - Page number markers (e.g., "F-14", "Page 42")
      - Repeated table-of-contents entries
      - Irregular whitespace and line breaks
      - Boilerplate "Table of Contents" headers
      - Unicode control characters

    Args:
        raw_text: Raw text extracted from BeautifulSoup

    Returns:
        Cleaned, normalized text string
    """
    # Remove any remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", raw_text)

    # Normalize unicode (NFKC: compatibility decomposition + composition)
    text = unicodedata.normalize("NFKC", text)

    # Remove page number markers: "F-14", "S-1", "Page 42", "- 14 -"
    text = re.sub(r"\bF-\d+\b", " ", text)
    text = re.sub(r"\bS-\d+\b", " ", text)
    text = re.sub(r"\bPage\s+\d+\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s-\s*\d+\s*-\s", " ", text)

    # Remove "Table of Contents" headings and their immediate context
    text = re.sub(r"(?i)table\s+of\s+contents\s*", " ", text)

    # Collapse multiple whitespace characters into a single space
    text = re.sub(r"[ \t]+", " ", text)

    # Collapse multiple newlines into a maximum of two
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove lines that are just whitespace
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    text = "\n".join(lines)

    # Final trim
    return text.strip()


def _split_into_chunks(text: str) -> list[str]:
    """
    Split text into overlapping word chunks.

    Target chunk size: settings.CHUNK_TARGET_WORDS (default 650 words)
    Overlap: settings.CHUNK_OVERLAP_WORDS (default 100 words)

    The overlap ensures that sentences near a chunk boundary appear in both
    adjacent chunks, preventing important content from being cut off.

    Args:
        text: Cleaned text to split

    Returns:
        List of text chunk strings
    """
    words = text.split()
    if not words:
        return []

    target = settings.CHUNK_TARGET_WORDS
    overlap = settings.CHUNK_OVERLAP_WORDS
    step = target - overlap  # words to advance per chunk

    if len(words) <= settings.CHUNK_MAX_WORDS:
        # Text fits in a single chunk
        return [text] if len(words) >= settings.CHUNK_MIN_WORDS // 2 else []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + target, len(words))
        chunk_words = words[start:end]
        chunk_text = " ".join(chunk_words)
        chunks.append(chunk_text)
        if end >= len(words):
            break
        start += step

    return chunks


def _generate_embeddings(chunks: list[str]) -> list[list[float]]:
    """
    Generate embedding vectors for a list of text chunks.

    Uses sentence-transformers all-MiniLM-L6-v2 in batches.
    Model runs fully offline (TRANSFORMERS_OFFLINE=1 must be set).

    Args:
        chunks: List of text strings to embed

    Returns:
        List of embedding vectors (one float list per chunk)
    """
    model = _get_embedding_model()
    embeddings = model.encode(
        chunks,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [emb.tolist() for emb in embeddings]


def _store_chunks_in_chromadb(
    chunks: list[str],
    chunk_ids: list[str],
    metadatas: list[dict],
    logger: AuditLogger,
) -> int:
    """
    Generate embeddings and store chunks in ChromaDB.

    Validates each ChunkMetadata through Pydantic before storage.
    Stores in batches to avoid ChromaDB size limits.

    Args:
        chunks: Text chunks to store
        chunk_ids: Unique ID for each chunk
        metadatas: Metadata dict for each chunk (must contain all 7 required fields)
        logger: AuditLogger for this run

    Returns:
        Number of chunks successfully stored
    """
    if not chunks:
        return 0

    # Validate metadata via Pydantic before touching ChromaDB
    validated_metadatas: list[dict] = []
    valid_chunks: list[str] = []
    valid_ids: list[str] = []

    for i, (chunk_text, chunk_id, meta) in enumerate(zip(chunks, chunk_ids, metadatas)):
        try:
            validated = ChunkMetadata(**meta)
            validated_metadatas.append(validated.to_chromadb_dict())
            valid_chunks.append(chunk_text)
            valid_ids.append(chunk_id)
        except Exception as e:
            logger.warning(
                "ValidateChunkMetadata",
                f"Chunk {chunk_id} failed metadata validation: {e}",
            )

    if not valid_chunks:
        return 0

    # Generate embeddings
    t0 = time.monotonic()
    embeddings = _generate_embeddings(valid_chunks)
    duration_ms = logger.elapsed_ms(t0)

    # Store in ChromaDB in batches of 100 (safe limit)
    collection = get_collection()
    batch_size = 100
    stored = 0

    for batch_start in range(0, len(valid_chunks), batch_size):
        batch_end = batch_start + batch_size
        try:
            collection.add(
                ids=valid_ids[batch_start:batch_end],
                documents=valid_chunks[batch_start:batch_end],
                embeddings=embeddings[batch_start:batch_end],
                metadatas=validated_metadatas[batch_start:batch_end],
            )
            stored += len(valid_ids[batch_start:batch_end])
        except Exception as e:
            logger.error(
                "ChromaDBStore",
                f"Batch storage failed at offset {batch_start}: {e}",
            )

    return stored


# ══════════════════════════════════════════════════════════════════════════
# 10-K Processing
# ══════════════════════════════════════════════════════════════════════════

def _extract_ten_k_sections(soup: BeautifulSoup) -> dict[str, str]:
    """
    Extract the text of each relevant 10-K section from the parsed HTML.

    Scans the document for Item headings (e.g., "Item 1.", "ITEM 1A.") and
    extracts the text between consecutive Item markers. Only returns sections
    defined in TEN_K_SECTIONS (Items 1, 1A, 3, 7, 7A, 8 Notes).

    The function is defensive: if section boundaries cannot be cleanly
    identified, it returns whatever partial content it can find.

    Args:
        soup: Parsed BeautifulSoup document

    Returns:
        Dict mapping section_code to extracted text
    """
    # Remove script, style, and XBRL namespace elements
    for tag in soup(["script", "style", "ix:hidden", "xbrli:xbrl"]):
        tag.decompose()

    # Get the full text of the document
    full_text = soup.get_text(separator="\n")
    cleaned_full = _clean_text(full_text)

    sections: dict[str, str] = {}

    # Item heading patterns — matches "Item 1.", "ITEM 1A.", "Item 1A —", etc.
    item_pattern = re.compile(
        r"(?:^|\n)\s*(?:ITEM|Item)\s+(\d+[A-Za-z]?)\s*[.:\-—]?\s*([^\n]{0,100})",
        re.MULTILINE,
    )

    matches = list(item_pattern.finditer(cleaned_full))
    if not matches:
        return sections

    # Map normalized item numbers to their positions
    item_positions: list[tuple[str, int, int]] = []  # (item_key, start, end)
    for match in matches:
        item_num = match.group(1).strip().lower().replace(" ", "")
        section_key = f"item_{item_num}"
        item_positions.append((section_key, match.start(), match.end()))

    # Extract text between consecutive item markers
    for i, (section_key, _start, content_start) in enumerate(item_positions):
        if section_key in TEN_K_SKIP_ITEMS:
            continue

        # Determine which section_code this maps to
        mapped_code = None
        for code in TEN_K_SECTIONS:
            if code == section_key or (
                section_key == "item_8" and code == "item_8_notes"
            ):
                mapped_code = code
                break

        if mapped_code is None:
            continue

        # End of this section = start of next item (or end of document)
        if i + 1 < len(item_positions):
            content_end = item_positions[i + 1][1]
        else:
            content_end = len(cleaned_full)

        section_text = cleaned_full[content_start:content_end].strip()

        # Skip very short sections (likely just headings with no content)
        if len(section_text.split()) < 50:
            continue

        sections[mapped_code] = section_text

    return sections


def process_ten_k(
    raw_bytes: bytes,
    filing: FilingRecord,
    ticker: str,
    fiscal_year: int,
    logger: AuditLogger,
) -> int:
    """
    Full pipeline for a single 10-K filing: parse → extract → clean → chunk → embed → store.

    Args:
        raw_bytes: Raw HTML bytes of the 10-K document
        filing: FilingRecord metadata for this filing
        ticker: Uppercase company ticker
        fiscal_year: Fiscal year this 10-K covers (integer, calendar year of FY end)
        logger: AuditLogger for this run

    Returns:
        Total number of chunks stored in ChromaDB for this filing
    """
    t0 = time.monotonic()
    ticker = ticker.upper().strip()

    # Detect XHTML and parse
    raw_text_sample = raw_bytes[:2000].decode("utf-8", errors="ignore")
    is_xhtml = "<?xml" in raw_text_sample or "xmlns=" in raw_text_sample
    soup = _parse_html(raw_bytes, is_xhtml)

    # Extract sections
    sections = _extract_ten_k_sections(soup)

    if not sections:
        logger.warning(
            "Process10K",
            f"No sections extracted from 10-K {filing.filing_date}",
            logger.elapsed_ms(t0),
        )
        return 0

    total_stored = 0

    for section_code, section_text in sections.items():
        chunks = _split_into_chunks(section_text)
        if not chunks:
            continue

        chunk_ids = [
            f"{ticker}_{fiscal_year}_10k_{section_code}_chunk_{idx:03d}"
            for idx in range(len(chunks))
        ]

        metadatas = [
            {
                "ticker": ticker,
                "filing_type": "10-K",
                "section_code": section_code,
                "fiscal_year": fiscal_year,
                "filing_date": filing.filing_date,
                "priority": "STANDARD",
                "event_type": "",
            }
            for _ in chunks
        ]

        stored = _store_chunks_in_chromadb(chunks, chunk_ids, metadatas, logger)
        total_stored += stored

        logger.success(
            "Process10KSection",
            f"{ticker} {fiscal_year} {section_code}: {stored} chunks stored",
        )

    logger.success(
        "Process10K",
        f"{ticker} 10-K {fiscal_year}: {total_stored} total chunks",
        logger.elapsed_ms(t0),
    )
    return total_stored


# ══════════════════════════════════════════════════════════════════════════
# 8-K Processing
# ══════════════════════════════════════════════════════════════════════════

def _extract_event_type(raw_bytes: bytes) -> str:
    """
    Extract the primary Item number from an 8-K filing.

    8-K filings declare their Item number(s) in the document body.
    This function extracts the first/primary Item code and maps it
    to the human-readable event_type string from EVENT_TYPE_MAP.

    Args:
        raw_bytes: Raw bytes of the 8-K document

    Returns:
        Event type string (e.g., "Departure or Appointment of Principal Officers")
        or empty string if not identifiable
    """
    text_sample = raw_bytes[:5000].decode("utf-8", errors="ignore")

    # Pattern: "Item 5.02" or "Item 2.02" etc.
    item_pattern = re.compile(r"Item\s+(\d+\.\d+)", re.IGNORECASE)
    matches = item_pattern.findall(text_sample)

    for match in matches:
        if match in EVENT_TYPE_MAP:
            return match

    # Try the full document if not found in sample
    full_text = raw_bytes.decode("utf-8", errors="ignore")
    all_matches = item_pattern.findall(full_text)
    for match in all_matches:
        if match in EVENT_TYPE_MAP:
            return match

    return ""


def process_eight_k(
    raw_bytes: bytes,
    filing: FilingRecord,
    ticker: str,
    logger: AuditLogger,
) -> int:
    """
    Full pipeline for a single 8-K filing: classify → parse → clean → chunk → embed → store.

    8-K filings are processed as a whole (no section splitting). The event type
    is extracted from the Item number and stored as metadata.

    Args:
        raw_bytes: Raw HTML bytes of the 8-K document
        filing: FilingRecord metadata for this filing
        ticker: Uppercase company ticker
        logger: AuditLogger for this run

    Returns:
        Number of chunks stored in ChromaDB for this filing
    """
    t0 = time.monotonic()
    ticker = ticker.upper().strip()

    # Classify the event type
    event_type_code = _extract_event_type(raw_bytes)
    event_type_label = EVENT_TYPE_MAP.get(event_type_code, "Other Events")

    # Parse and extract text
    raw_text_sample = raw_bytes[:2000].decode("utf-8", errors="ignore")
    is_xhtml = "<?xml" in raw_text_sample or "xmlns=" in raw_text_sample
    soup = _parse_html(raw_bytes, is_xhtml)

    # Remove script, style, exhibit listings
    for tag in soup(["script", "style"]):
        tag.decompose()

    raw_text = soup.get_text(separator="\n")
    cleaned_text = _clean_text(raw_text)

    if len(cleaned_text.split()) < 50:
        logger.warning(
            "Process8K",
            f"8-K {filing.filing_date} has insufficient text after cleaning",
            logger.elapsed_ms(t0),
        )
        return 0

    chunks = _split_into_chunks(cleaned_text)
    if not chunks:
        return 0

    # Chunk IDs include last 6 digits of accession to prevent collision
    # when two 8-Ks are filed on the same date
    chunk_ids = [
        f"{ticker}_8k_{filing.filing_date.replace('-', '')}_{filing.accession_last_six}_chunk_{idx:03d}"
        for idx in range(len(chunks))
    ]

    metadatas = [
        {
            "ticker": ticker,
            "filing_type": "8-K",
            "section_code": "8k_body",
            "fiscal_year": 0,  # 8-K uses 0 per Contract 6
            "filing_date": filing.filing_date,
            "priority": "STANDARD",
            "event_type": event_type_code,
        }
        for _ in chunks
    ]

    stored = _store_chunks_in_chromadb(chunks, chunk_ids, metadatas, logger)

    logger.success(
        "Process8K",
        f"{ticker} 8-K {filing.filing_date} ({event_type_label}): {stored} chunks stored",
        logger.elapsed_ms(t0),
    )
    return stored
