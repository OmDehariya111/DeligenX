"""
tools/ingestion/user_file_processor.py — Phase 3: Optional User File Processing
Agent: Agent 1 (Ingestion Agent)
Reads: User-provided file (PDF or .txt)
Writes: ChromaDB vector store (data/chromadb/) — priority: HIGH chunks

Processes the optional user-supplied file. Supports:
  - PDF (.pdf): text extraction page by page via pypdf
  - Plain text (.txt): direct read

Runs the same clean → chunk → embed → store pipeline as text_processor.py.
User file chunks get priority='HIGH' to signal to downstream agents that
this content was deliberately provided and should receive extra weight.
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.config import settings
from core.logger import AuditLogger
from schemas.ingestion_schemas import ChunkMetadata
from tools.ingestion.text_processor import (
    _clean_text,
    _split_into_chunks,
    _store_chunks_in_chromadb,
)


def _extract_pdf_text(file_path: Path, logger: AuditLogger) -> Optional[str]:
    """
    Extract all text from a PDF file using pypdf.

    Extracts text page by page. If a page fails, logs a warning and continues
    with the remaining pages — never aborts for a single failed page.

    Args:
        file_path: Absolute path to the PDF file
        logger: AuditLogger for this run

    Returns:
        Extracted text as a single string, or None if the file cannot be read
    """
    try:
        import pypdf
    except ImportError:
        logger.error("ExtractPDF", "pypdf is not installed. Cannot process PDF files.")
        return None

    text_parts: list[str] = []
    t0 = time.monotonic()

    try:
        reader = pypdf.PdfReader(str(file_path))
        total_pages = len(reader.pages)

        for page_num, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text()
                if page_text and page_text.strip():
                    text_parts.append(page_text)
            except Exception as e:
                logger.warning(
                    "ExtractPDF",
                    f"Failed to extract text from page {page_num + 1}/{total_pages}: {e}",
                )

        if not text_parts:
            logger.warning(
                "ExtractPDF",
                f"No text extracted from PDF: {file_path.name} "
                f"(may be scanned/image-based PDF)",
                logger.elapsed_ms(t0),
            )
            return None

        full_text = "\n\n".join(text_parts)
        logger.success(
            "ExtractPDF",
            f"{file_path.name}: extracted {len(text_parts)}/{total_pages} pages",
            logger.elapsed_ms(t0),
        )
        return full_text

    except Exception as e:
        logger.error(
            "ExtractPDF",
            f"Failed to read PDF {file_path.name}: {e}",
            logger.elapsed_ms(t0),
        )
        return None


def _extract_txt_text(file_path: Path, logger: AuditLogger) -> Optional[str]:
    """
    Read text directly from a plain text (.txt) file.

    Attempts UTF-8 encoding first, falls back to latin-1 if that fails.

    Args:
        file_path: Absolute path to the text file
        logger: AuditLogger for this run

    Returns:
        File contents as string, or None if the file cannot be read
    """
    t0 = time.monotonic()
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = file_path.read_text(encoding=encoding)
            logger.success(
                "ExtractTXT",
                f"{file_path.name}: {len(text)} characters (encoding: {encoding})",
                logger.elapsed_ms(t0),
            )
            return text
        except UnicodeDecodeError:
            continue
        except OSError as e:
            logger.error("ExtractTXT", f"Cannot read {file_path.name}: {e}", logger.elapsed_ms(t0))
            return None

    logger.error(
        "ExtractTXT",
        f"Could not decode {file_path.name} with any supported encoding",
        logger.elapsed_ms(t0),
    )
    return None


def process_user_file(
    file_path: Path,
    ticker: str,
    logger: AuditLogger,
) -> int:
    """
    Process the optional user-supplied file and store its chunks in ChromaDB.

    Supports .pdf and .txt files. Any other extension is rejected with a clear
    error message.

    Chunks from user files get:
      - filing_type = "USER_FILE"  (Contract 6 value — not "USER_PROVIDED")
      - priority = "HIGH"          (user deliberately selected this file)
      - fiscal_year = 0            (not tied to a specific year)
      - event_type = ""            (not an SEC event)
      - section_code = "user_file"

    Args:
        file_path: Absolute path to the user's file
        ticker: Uppercase company ticker
        logger: AuditLogger for this run

    Returns:
        Number of chunks stored in ChromaDB (0 if processing failed)
    """
    ticker = ticker.upper().strip()
    t0 = time.monotonic()

    if not file_path.exists():
        logger.error("ProcessUserFile", f"File not found: {file_path}")
        return 0

    suffix = file_path.suffix.lower()

    # ── Extract text based on file type ───────────────────────────────────
    if suffix == ".pdf":
        raw_text = _extract_pdf_text(file_path, logger)
    elif suffix == ".txt":
        raw_text = _extract_txt_text(file_path, logger)
    else:
        logger.error(
            "ProcessUserFile",
            f"Unsupported file type: '{suffix}'. Only .pdf and .txt are supported.",
        )
        return 0

    if raw_text is None:
        logger.error("ProcessUserFile", f"Text extraction failed for {file_path.name}")
        return 0

    # ── Clean, chunk, embed, store ─────────────────────────────────────────
    cleaned_text = _clean_text(raw_text)
    if len(cleaned_text.split()) < 20:
        logger.warning(
            "ProcessUserFile",
            f"File {file_path.name} produced fewer than 20 words after cleaning",
        )
        return 0

    chunks = _split_into_chunks(cleaned_text)
    if not chunks:
        logger.warning("ProcessUserFile", f"No chunks produced from {file_path.name}")
        return 0

    upload_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    chunk_ids = [
        f"{ticker}_userfile_{upload_date.replace('-', '')}_chunk_{idx:03d}"
        for idx in range(len(chunks))
    ]

    metadatas = [
        {
            "ticker": ticker,
            "filing_type": "USER_FILE",
            "section_code": "user_file",
            "fiscal_year": 0,
            "filing_date": upload_date,
            "priority": "HIGH",
            "event_type": "",
        }
        for _ in chunks
    ]

    stored = _store_chunks_in_chromadb(chunks, chunk_ids, metadatas, logger)

    logger.success(
        "ProcessUserFile",
        f"{file_path.name}: {stored} chunks stored with priority=HIGH",
        logger.elapsed_ms(t0),
    )
    return stored
