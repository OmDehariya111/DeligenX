"""
run_ingestion.py — DeligenX CLI Entry Point for Agent 1
Agent: Agent 1 (Ingestion Agent)
Usage:
    python run_ingestion.py AAPL
    python run_ingestion.py MSFT --file report.pdf
    python run_ingestion.py TSLA --force-refresh
    python run_ingestion.py NVDA --file analysis.txt --force-refresh

Normalizes the ticker (upper().strip()), then delegates to the Ingestion Agent.
Prints a concise summary to stdout on completion.
"""

import argparse
import io
import json
import sys
from pathlib import Path

# Force UTF-8 output on Windows (prevents UnicodeEncodeError with special characters)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments for the ingestion pipeline.

    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        prog="run_ingestion",
        description="DeligenX Ingestion Agent — collect and structure financial data",
    )
    parser.add_argument(
        "ticker",
        type=str,
        help="US stock ticker symbol (e.g., AAPL, MSFT, TSLA)",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        metavar="FILE_PATH",
        help="Optional supplementary file (PDF or .txt) to include in the analysis",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        default=False,
        help="Bypass cache and re-collect all data from scratch",
    )
    return parser.parse_args()


def main() -> int:
    """
    Main entry point for the CLI.

    Returns:
        Exit code: 0 on success, 1 on failure
    """
    args = parse_args()

    # Normalize ticker
    ticker = args.ticker.upper().strip()

    # Validate optional file
    file_path: Path | None = None
    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"ERROR: File not found: {args.file}", file=sys.stderr)
            return 1
        if file_path.suffix.lower() not in (".pdf", ".txt"):
            print(
                f"ERROR: Unsupported file type '{file_path.suffix}'. "
                "Only .pdf and .txt are supported.",
                file=sys.stderr,
            )
            return 1

    print(f"\n{'=' * 60}")
    print(f"  DeligenX Ingestion Agent")
    print(f"  Ticker:        {ticker}")
    print(f"  Supplementary: {file_path.name if file_path else 'None'}")
    print(f"  Force refresh: {args.force_refresh}")
    print(f"{'=' * 60}\n")

    # Run the ingestion pipeline
    from agents.ingestion_agent import run_ingestion

    summary = run_ingestion(
        ticker=ticker,
        file_path=file_path,
        force_refresh=args.force_refresh,
    )

    if summary is None:
        print(f"\n[FAILED] Could not resolve ticker '{ticker}' in SEC EDGAR.")
        print("   Check the ticker symbol and try again.")
        return 1

    # Print completion summary
    print(f"\n{'=' * 60}")
    print(f"  INGESTION COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Status:          {summary.run_status}")
    print(f"  Company:         {summary.company_name} ({summary.ticker})")
    print(f"  CIK:             {summary.cik}")
    print(f"  SIC:             {summary.sic_code} — {summary.industry_name}")
    print(f"  FY End Month:    {summary.fiscal_year_end_month}")
    print(f"  Years Covered:   {summary.years_covered}")
    
    from schemas.financial_fields import FIELD_DEFINITIONS
    total_fields = len(FIELD_DEFINITIONS)
    print(f"  Fields:          {summary.fields_with_data}/{total_fields} populated")
    print(f"  Vector DB:       {summary.vector_db_stats.total_chunks} chunks "
          f"({summary.vector_db_stats.chunks_10k} 10-K + "
          f"{summary.vector_db_stats.chunks_8k} 8-K + "
          f"{summary.vector_db_stats.chunks_user_file} user)")
    print(f"  Duration:        {summary.ingestion_duration_sec}s")
    print(f"  Warnings:        {len(summary.warnings)}")
    print(f"  Errors:          {len(summary.errors)}")

    if summary.missing_critical_fields:
        high_count = sum(1 for f in summary.missing_critical_fields if f.criticality == "HIGH")
        if high_count > 0:
            print(f"\n  [!] {high_count} HIGH-criticality fields missing:")
            for entry in summary.missing_critical_fields:
                if entry.criticality == "HIGH":
                    print(f"     - {entry.field}: {entry.impact}")

    from core.config import settings
    summary_path = settings.ticker_output_path(ticker) / "ingestion_summary.json"
    print(f"\n  Summary written: {summary_path}")
    print(f"{'=' * 60}\n")

    return 0 if summary.run_status in ("SUCCESS", "PARTIAL") else 1


if __name__ == "__main__":
    sys.exit(main())
