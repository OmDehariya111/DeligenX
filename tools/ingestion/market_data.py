"""
tools/ingestion/market_data.py — Phase 4: Market Data from yfinance
Agent: Agent 1 (Ingestion Agent)
Reads: yfinance (stock prices, beta)
Writes: Nothing (populates FinancialYearData objects)

Uses yfinance ONLY for:
  - Stock closing price on each fiscal year end date (for 5 years)
  - Beta coefficient (single current value from .info)

yfinance is NEVER used for fundamental financial data (income statement,
balance sheet, cash flow). All fundamental data comes from SEC CompanyFacts.

On any yfinance failure: logs WARNING, returns None — never raises.
"""

import time
from typing import Optional

from core.logger import AuditLogger


def fetch_fy_end_prices(
    ticker: str,
    fiscal_year_end_dates: dict[int, str],
    logger: AuditLogger,
) -> dict[int, Optional[float]]:
    """
    Fetch the closing stock price on each fiscal year end date.

    For each fiscal year, uses yfinance to get the historical closing price
    on the exact date (or the nearest prior trading day if the date was
    a weekend/holiday).

    Args:
        ticker: Uppercase ticker symbol (e.g., "AAPL")
        fiscal_year_end_dates: Dict mapping fiscal_year (int) → ISO date string
                               e.g., {2024: "2024-09-28", 2023: "2023-09-30"}
        logger: AuditLogger for this run

    Returns:
        Dict mapping fiscal_year → closing price (float) or None if unavailable
    """
    import yfinance as yf
    from datetime import datetime, timedelta

    ticker = ticker.upper().strip()
    prices: dict[int, Optional[float]] = {}

    if not fiscal_year_end_dates:
        return prices

    try:
        stock = yf.Ticker(ticker)

        for fy, date_str in fiscal_year_end_dates.items():
            t0 = time.monotonic()
            try:
                # Fetch a small window around the FY end date
                # (in case the exact date was a weekend/holiday)
                date_dt = datetime.strptime(date_str, "%Y-%m-%d")
                start_date = (date_dt - timedelta(days=5)).strftime("%Y-%m-%d")
                end_date = (date_dt + timedelta(days=2)).strftime("%Y-%m-%d")

                hist = stock.history(start=start_date, end=end_date)

                if hist.empty:
                    logger.warning(
                        "FetchFYPrice",
                        f"{ticker} FY{fy}: no price data for {date_str}",
                        logger.elapsed_ms(t0),
                    )
                    prices[fy] = None
                    continue

                # Use the last available trading day on or before the FY end date
                hist.index = hist.index.tz_localize(None)  # Remove timezone for comparison
                mask = hist.index <= date_dt
                if not mask.any():
                    # No trading day on or before the date — use the first available
                    closing_price = float(hist["Close"].iloc[0])
                else:
                    closing_price = float(hist.loc[mask, "Close"].iloc[-1])

                prices[fy] = closing_price
                logger.success(
                    "FetchFYPrice",
                    f"{ticker} FY{fy} ({date_str}): ${closing_price:.2f}",
                    logger.elapsed_ms(t0),
                )

            except ValueError as e:
                logger.warning(
                    "FetchFYPrice",
                    f"{ticker} FY{fy}: date parse error for '{date_str}': {e}",
                    logger.elapsed_ms(t0),
                )
                prices[fy] = None

            except Exception as e:
                logger.warning(
                    "FetchFYPrice",
                    f"{ticker} FY{fy}: yfinance error for {date_str}: {e}",
                    logger.elapsed_ms(t0),
                )
                prices[fy] = None

    except Exception as e:
        logger.warning(
            "FetchFYPrices",
            f"{ticker}: yfinance Ticker initialization failed: {e}",
        )
        return {fy: None for fy in fiscal_year_end_dates}

    return prices


def fetch_beta(ticker: str, logger: AuditLogger) -> Optional[float]:
    """
    Fetch the beta coefficient for a stock from yfinance.

    Beta is a single value (not year-specific): the 5-year monthly regression
    against the S&P 500, calculated by Yahoo Finance. It is supplementary context
    and its absence does not block any downstream calculations.

    Args:
        ticker: Uppercase ticker symbol
        logger: AuditLogger for this run

    Returns:
        Beta as a float, or None if unavailable
    """
    import yfinance as yf

    ticker = ticker.upper().strip()
    t0 = time.monotonic()

    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        beta = info.get("beta")
        if beta is None:
            logger.warning(
                "FetchBeta",
                f"{ticker}: beta not available in yfinance info",
                logger.elapsed_ms(t0),
            )
            return None

        beta_float = float(beta)
        logger.success(
            "FetchBeta",
            f"{ticker}: beta = {beta_float:.3f} (5-yr monthly vs S&P 500)",
            logger.elapsed_ms(t0),
        )
        return beta_float

    except Exception as e:
        logger.warning(
            "FetchBeta",
            f"{ticker}: yfinance error fetching beta: {e}",
            logger.elapsed_ms(t0),
        )
        return None


def get_fiscal_year_end_dates(year_data: dict) -> dict[int, str]:
    """
    Extract fiscal year end date strings from FinancialYearData objects.

    Args:
        year_data: Dict mapping fiscal_year → FinancialYearData

    Returns:
        Dict mapping fiscal_year → ISO date string (e.g., "2024-09-28")
        Only includes years where fiscal_year_end_date is not None.
    """
    return {
        fy: fyd.fiscal_year_end_date
        for fy, fyd in year_data.items()
        if fyd.fiscal_year_end_date is not None
    }
