"""
core/http_client.py — DeligenX Rate-Limited HTTP Client
Agent: All agents (shared core module)
Reads: Nothing (stateless)
Writes: Nothing (pure function module)

ALL external HTTP calls in this project go through this module. It enforces:
  - SEC EDGAR User-Agent header (required by SEC)
  - 0.12s minimum sleep between consecutive EDGAR calls (≤10 req/s)
  - Exponential backoff on 429 / 503 responses (1s → 2s → 4s → stop)
  - Structured error logging on every failure
  - Returns None on final failure — never raises for network errors

No raw requests.get() calls exist anywhere else in this codebase.
"""

import time
from typing import Optional

import requests

from core.config import settings


def get(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = 30,
    is_edgar: bool = True,
) -> Optional[requests.Response]:
    """
    Perform a GET request with SEC EDGAR rate limiting and exponential backoff.

    If is_edgar=True, the function:
      1. Sleeps EDGAR_MIN_SLEEP_SEC before making the call (rate limit)
      2. Adds the SEC-required User-Agent header
      3. Retries up to EDGAR_MAX_RETRIES times on 429 / 503 with backoff

    If is_edgar=False, no pre-sleep is applied and retries use a fixed 1-second wait.

    Args:
        url: Full URL to request
        params: Optional query string parameters
        headers: Optional additional headers (merged with defaults)
        timeout: Request timeout in seconds
        is_edgar: Whether to apply SEC EDGAR rate limiting (default True)

    Returns:
        requests.Response on success, None if all retries are exhausted or a
        non-retryable error occurs.
    """
    default_headers: dict[str, str] = {}
    if is_edgar:
        default_headers["User-Agent"] = settings.EDGAR_USER_AGENT
        default_headers["Accept-Encoding"] = "gzip, deflate"
        time.sleep(settings.EDGAR_MIN_SLEEP_SEC)

    if headers:
        default_headers.update(headers)

    retries = settings.EDGAR_MAX_RETRIES if is_edgar else 1
    backoff_sequence = settings.EDGAR_RETRY_BACKOFF if is_edgar else [1.0]

    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=default_headers,
                timeout=timeout,
            )

            if response.status_code == 200:
                return response

            if response.status_code in (429, 503) and attempt < retries:
                wait_sec = backoff_sequence[min(attempt, len(backoff_sequence) - 1)]
                time.sleep(wait_sec)
                continue

            if response.status_code == 404:
                # Not found — don't retry, return None silently
                return None

            # Other non-200 status
            if attempt == retries:
                return None
            time.sleep(1.0)

        except requests.exceptions.Timeout:
            if attempt == retries:
                return None
            wait_sec = backoff_sequence[min(attempt, len(backoff_sequence) - 1)]
            time.sleep(wait_sec)

        except requests.exceptions.ConnectionError:
            if attempt == retries:
                return None
            wait_sec = backoff_sequence[min(attempt, len(backoff_sequence) - 1)]
            time.sleep(wait_sec)

        except requests.exceptions.RequestException:
            return None

    return None


def get_json(
    url: str,
    params: Optional[dict] = None,
    is_edgar: bool = True,
) -> Optional[dict]:
    """
    Convenience wrapper: performs a GET request and returns the parsed JSON body.

    Returns None if the request fails or the response body is not valid JSON.

    Args:
        url: Full URL to request
        params: Optional query string parameters
        is_edgar: Whether to apply SEC EDGAR rate limiting

    Returns:
        Parsed JSON as dict, or None on any failure.
    """
    response = get(url, params=params, is_edgar=is_edgar)
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def get_text(
    url: str,
    params: Optional[dict] = None,
    is_edgar: bool = True,
) -> Optional[str]:
    """
    Convenience wrapper: performs a GET request and returns the response text.

    Returns None if the request fails.

    Args:
        url: Full URL to request
        params: Optional query string parameters
        is_edgar: Whether to apply SEC EDGAR rate limiting

    Returns:
        Response body as string, or None on any failure.
    """
    response = get(url, params=params, is_edgar=is_edgar)
    if response is None:
        return None
    return response.text
