"""
core/logger.py — DeligenX Structured Audit Logger
Agent: All agents (shared core module)
Reads: Nothing
Writes: data/logs/ingestion_{ticker}_{timestamp}.jsonl (and equivalents per agent)

Every significant operation in the platform emits a structured JSONL log entry
with the 7 required fields: timestamp, ticker, agent_name, operation, outcome,
detail, and duration_ms. No scattered print() calls exist anywhere in this project.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.config import settings

# ── Standard Python logger (used internally for fallback / console output) ──
_py_logger = logging.getLogger("deligenx")
if not _py_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    _py_logger.addHandler(_handler)
    _py_logger.setLevel(logging.DEBUG)


class AuditLogger:
    """
    Structured audit logger that writes JSONL entries to disk and also emits
    to the Python logging system for console visibility.

    Each entry contains:
        timestamp       ISO 8601 UTC
        ticker          Company ticker being processed
        agent_name      Which agent is logging
        operation       What was being attempted
        outcome         SUCCESS | WARNING | ERROR
        detail          Specific data (URL, table name, count, exception message)
        duration_ms     How long the operation took (0 if not measured)
    """

    def __init__(self, agent_name: str, ticker: str) -> None:
        """
        Initialise the logger for a specific agent + ticker run.

        Args:
            agent_name: Human-readable agent identifier, e.g. "IngestionAgent"
            ticker: Uppercase ticker symbol, e.g. "AAPL"
        """
        self.agent_name = agent_name
        self.ticker = ticker.upper().strip()
        self._log_path: Optional[Path] = None
        self._start_time: float = time.monotonic()

    def _ensure_log_file(self) -> Path:
        """Lazily create the log file on first write."""
        if self._log_path is None:
            settings.ensure_directories()
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            filename = f"{self.agent_name.lower()}_{self.ticker}_{ts}.jsonl"
            self._log_path = settings.logs_path() / filename
        return self._log_path

    def _write(
        self,
        operation: str,
        outcome: str,
        detail: str,
        duration_ms: int = 0,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Write a single JSONL entry to the audit log file and Python logger.

        Args:
            operation: What was being attempted
            outcome: "SUCCESS", "WARNING", or "ERROR"
            detail: Specific data point (URL, table name, error message, etc.)
            duration_ms: Duration of the operation in milliseconds
            extra: Any additional key-value pairs to include in the entry
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticker": self.ticker,
            "agent_name": self.agent_name,
            "operation": operation,
            "outcome": outcome,
            "detail": detail,
            "duration_ms": duration_ms,
        }
        if extra:
            entry.update(extra)

        log_path = self._ensure_log_file()
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError as e:
            _py_logger.error("Failed to write audit log entry: %s", e)

        # Mirror to Python logger at appropriate level
        msg = "[%s] %s: %s — %s (%dms)" % (
            outcome, operation, detail, self.ticker, duration_ms
        )
        if outcome == "SUCCESS":
            _py_logger.info(msg)
        elif outcome == "WARNING":
            _py_logger.warning(msg)
        else:
            _py_logger.error(msg)

    def success(self, operation: str, detail: str, duration_ms: int = 0) -> None:
        """Log a successful operation."""
        self._write(operation, "SUCCESS", detail, duration_ms)

    def warning(self, operation: str, detail: str, duration_ms: int = 0) -> None:
        """Log a non-fatal warning (missing data, fallback used, etc.)."""
        self._write(operation, "WARNING", detail, duration_ms)

    def error(self, operation: str, detail: str, duration_ms: int = 0) -> None:
        """Log a fatal or significant error."""
        self._write(operation, "ERROR", detail, duration_ms)

    def elapsed_ms(self, since: float) -> int:
        """
        Return elapsed milliseconds since a monotonic reference time.

        Args:
            since: Value returned by time.monotonic() at operation start
        Returns:
            Elapsed time in milliseconds as an integer
        """
        return int((time.monotonic() - since) * 1000)

    def total_duration_sec(self) -> int:
        """Return total seconds elapsed since this logger was created."""
        return int(time.monotonic() - self._start_time)


class TimedOperation:
    """
    Context manager for timing an operation and automatically logging its outcome.

    Usage:
        with TimedOperation(logger, "DownloadFiling", "10-K 2024") as op:
            result = do_work()
            op.set_detail(f"Downloaded {len(result)} bytes")
        # Logs SUCCESS with duration on __exit__
        # To log a WARNING or ERROR, call op.mark_warning(detail) or op.mark_error(detail)
    """

    def __init__(self, logger: AuditLogger, operation: str, initial_detail: str = "") -> None:
        """
        Initialise a timed operation context manager.

        Args:
            logger: AuditLogger instance to emit to
            operation: Name of the operation being timed
            initial_detail: Starting detail string (can be updated with set_detail)
        """
        self.logger = logger
        self.operation = operation
        self.detail = initial_detail
        self._outcome = "SUCCESS"
        self._start: float = 0.0

    def set_detail(self, detail: str) -> None:
        """Update the detail string mid-operation."""
        self.detail = detail

    def mark_warning(self, detail: str) -> None:
        """Mark this operation as a warning."""
        self._outcome = "WARNING"
        self.detail = detail

    def mark_error(self, detail: str) -> None:
        """Mark this operation as an error."""
        self._outcome = "ERROR"
        self.detail = detail

    def __enter__(self) -> "TimedOperation":
        """Start timing."""
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Log the outcome and duration. Does not suppress exceptions."""
        duration_ms = self.logger.elapsed_ms(self._start)
        if exc_type is not None:
            self.logger.error(
                self.operation,
                f"{self.detail} — Exception: {exc_val!r}",
                duration_ms,
            )
        else:
            self.logger._write(self.operation, self._outcome, self.detail, duration_ms)
        return False  # Never suppress exceptions
