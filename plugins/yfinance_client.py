"""
yfinance client — TSX equities (permanent) and VIX/VVIX (free-tier fallback).

TSX tickers always route here regardless of Polygon tier.
VIX/VVIX route here when POLYGON_TIER = "free" (controlled by config.VIX_SOURCE).

No API key required. Uses 0.5s polite spacing between calls — not a rate
limiter, just courteous pacing for an unofficial API.
"""

from __future__ import annotations

import time

import pandas as pd
import yfinance as yf

from plugins.base_client import BaseMarketClient


class YFinanceClient(BaseMarketClient):
    def fetch_ohlcv(self, ticker: str, start: str, end: str) -> list[dict]:
        """
        Fetch OHLCV daily bars via yfinance.
        Returns a list of dicts in the same shape as PolygonClient normalises to,
        so ingest tasks handle both sources identically.

        end is exclusive in yfinance — caller passes the target date for both
        start and end; we add one day to end internally.
        """
        end_dt = pd.Timestamp(end) + pd.Timedelta(days=1)
        df = yf.Ticker(ticker).history(
            start=start,
            end=end_dt.strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=True,
        )

        time.sleep(0.5)

        if df.empty:
            return []

        currency = "CAD" if ticker.endswith(".TO") else "USD"
        bars = []
        for ts, row in df.iterrows():
            bars.append(
                {
                    "ticker": ticker,
                    "date": ts.strftime("%Y-%m-%d"),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row.get("Volume", 0)),
                    "currency": currency,
                    "source": "yfinance",
                }
            )
        return bars

    def fetch_metadata(self, ticker: str) -> dict:
        """Fetch company metadata via yfinance .info."""
        info = yf.Ticker(ticker).info
        time.sleep(0.5)
        return {
            "name": info.get("longName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap": info.get("marketCap"),
            "exchange": "TSX" if ticker.endswith(".TO") else info.get("exchange"),
        }
