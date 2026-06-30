"""
tests/test_chromadb_metadata.py — Tests for ChromaDB metadata validation
Agent: Agent 1 (Ingestion Agent)

Tests:
  1. test_chunkmetadata_validates_all_7_required_fields
  2. test_chunkmetadata_rejects_invalid_filing_type
  3. test_chunkmetadata_normalizes_ticker_to_uppercase
  4. test_eight_k_chunk_has_fiscal_year_zero
  5. test_user_file_chunk_has_priority_high_and_filing_type_user_file
"""

import pytest
from pydantic import ValidationError

from schemas.ingestion_schemas import ChunkMetadata
from tests.fixtures import (
    CHROMADB_REQUIRED_METADATA_FIELDS,
    EIGHT_K_FISCAL_YEAR,
    EIGHT_K_SECTION_CODE,
    VALID_FILING_TYPES,
    VALID_PRIORITY_VALUES,
)


# ── Valid chunk metadata examples ──────────────────────────────────────────

VALID_10K_METADATA = {
    "ticker": "AAPL",
    "filing_type": "10-K",
    "section_code": "item_7",
    "fiscal_year": 2024,
    "filing_date": "2024-11-01",
    "priority": "STANDARD",
    "event_type": "",
}

VALID_8K_METADATA = {
    "ticker": "AAPL",
    "filing_type": "8-K",
    "section_code": "8k_body",
    "fiscal_year": 0,  # Contract 6: must be 0 for 8-K
    "filing_date": "2024-09-15",
    "priority": "STANDARD",
    "event_type": "5.02",
}

VALID_USER_FILE_METADATA = {
    "ticker": "AAPL",
    "filing_type": "USER_FILE",
    "section_code": "user_file",
    "fiscal_year": 0,
    "filing_date": "2024-06-15",
    "priority": "HIGH",
    "event_type": "",
}


def test_chunkmetadata_validates_all_7_required_fields():
    """
    A valid ChunkMetadata with all 7 required fields must pass Pydantic validation.
    The to_chromadb_dict() output must contain all 7 field names.
    """
    chunk = ChunkMetadata(**VALID_10K_METADATA)
    chromadb_dict = chunk.to_chromadb_dict()

    for field in CHROMADB_REQUIRED_METADATA_FIELDS:
        assert field in chromadb_dict, (
            f"Required field '{field}' missing from to_chromadb_dict() output. "
            f"Available: {list(chromadb_dict.keys())}"
        )


def test_chunkmetadata_rejects_invalid_filing_type():
    """
    ChunkMetadata must reject filing_type values that are not in {10-K, 8-K, USER_FILE}.
    Should raise Pydantic ValidationError.
    """
    bad_metadata = VALID_10K_METADATA.copy()
    bad_metadata["filing_type"] = "USER_PROVIDED"  # Contract 6 violation

    with pytest.raises(ValidationError) as exc_info:
        ChunkMetadata(**bad_metadata)

    # Verify the error message mentions filing_type or the invalid value
    error_str = str(exc_info.value)
    assert "filing_type" in error_str or "USER_PROVIDED" in error_str, (
        f"ValidationError raised but didn't mention the invalid field. "
        f"Error: {error_str[:200]}"
    )


def test_chunkmetadata_normalizes_ticker_to_uppercase():
    """
    ChunkMetadata must normalize the ticker to uppercase during validation.
    Input 'aapl' → stored as 'AAPL'.
    """
    metadata = VALID_10K_METADATA.copy()
    metadata["ticker"] = "aapl"  # Intentionally lowercase

    chunk = ChunkMetadata(**metadata)
    assert chunk.ticker == "AAPL", (
        f"Expected ticker 'AAPL' after normalization, got {chunk.ticker!r}"
    )


def test_eight_k_chunk_has_fiscal_year_zero():
    """
    8-K chunks must have fiscal_year=0 per Block B Contract 6.
    The model must accept 0 as a valid value.
    """
    chunk = ChunkMetadata(**VALID_8K_METADATA)
    assert chunk.fiscal_year == EIGHT_K_FISCAL_YEAR, (
        f"8-K chunk fiscal_year must be {EIGHT_K_FISCAL_YEAR}, got {chunk.fiscal_year}"
    )
    assert chunk.section_code == EIGHT_K_SECTION_CODE, (
        f"8-K section_code must be '{EIGHT_K_SECTION_CODE}', got {chunk.section_code!r}"
    )


def test_user_file_chunk_has_priority_high_and_filing_type_user_file():
    """
    User file chunks must have:
      - filing_type = 'USER_FILE'  (not 'USER_PROVIDED' — Contract 6 decision)
      - priority = 'HIGH'
    """
    chunk = ChunkMetadata(**VALID_USER_FILE_METADATA)

    assert chunk.filing_type == "USER_FILE", (
        f"User file chunk filing_type must be 'USER_FILE', got {chunk.filing_type!r}. "
        "Contract 6 decision: 'USER_FILE' is the correct value."
    )
    assert chunk.priority == "HIGH", (
        f"User file chunk priority must be 'HIGH', got {chunk.priority!r}"
    )
