"""
core/chromadb_client.py — DeligenX ChromaDB Singleton Client
Agent: Agent 1 creates the collection. Agents 3, 4, 5 read from it.
Reads: Nothing on startup
Writes: data/chromadb/ (creates if absent)

ALL ChromaDB access in this project goes through this module.
No direct chromadb.Client() or chromadb.PersistentClient() instantiation
exists anywhere else in the codebase.

Design: The module maintains a single PersistentClient instance and provides
get_collection() which creates-or-gets the named collection. This is safe for
single-process use (which is our architecture — no concurrent agent writes).
"""

from typing import Optional

import chromadb
from chromadb import Collection

from core.config import settings


# ── Module-level ChromaDB client singleton ────────────────────────────────
_client: Optional[chromadb.PersistentClient] = None


def _get_client() -> chromadb.PersistentClient:
    """
    Lazily initialise and return the ChromaDB PersistentClient singleton.

    The client is created on the first call and reused for the lifetime of
    the process. The persistent store is located at settings.chromadb_path().
    """
    global _client
    if _client is None:
        chroma_path = settings.chromadb_path()
        chroma_path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(chroma_path))
    return _client


def get_collection(name: Optional[str] = None) -> Collection:
    """
    Return the ChromaDB collection, creating it if it does not exist.

    Uses the collection name from settings by default. All metadata and
    embedding configurations are set at collection creation time and are
    consistent for the life of the database.

    Args:
        name: Collection name. Defaults to settings.CHROMADB_COLLECTION_NAME.

    Returns:
        ChromaDB Collection object ready for add / query operations.
    """
    collection_name = name or settings.CHROMADB_COLLECTION_NAME
    client = _get_client()

    # get_or_create is idempotent — safe to call on every agent startup
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},  # cosine similarity for semantic search
    )
    return collection


def get_chunk_count(ticker: Optional[str] = None) -> int:
    """
    Return the total number of chunks stored in the collection, optionally
    filtered by ticker.

    Args:
        ticker: If provided, count only chunks for this ticker. If None,
                count all chunks in the collection.

    Returns:
        Integer chunk count.
    """
    collection = get_collection()

    if ticker is None:
        return collection.count()

    result = collection.get(
        where={"ticker": ticker.upper().strip()},
        include=[],  # Only need IDs to count
    )
    return len(result["ids"])


def delete_ticker_chunks(ticker: str) -> int:
    """
    Delete all chunks for a given ticker from the collection.

    Used by force_refresh to clear stale data before re-ingesting.

    Args:
        ticker: Uppercase ticker symbol

    Returns:
        Number of chunks deleted.
    """
    ticker = ticker.upper().strip()
    collection = get_collection()

    existing = collection.get(
        where={"ticker": ticker},
        include=[],
    )
    ids_to_delete = existing["ids"]

    if ids_to_delete:
        collection.delete(ids=ids_to_delete)

    return len(ids_to_delete)


def reset_client() -> None:
    """
    Reset the singleton client. Used in tests to get a fresh client instance.
    Should not be called in production code.
    """
    global _client
    _client = None
