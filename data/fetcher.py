from __future__ import annotations
import random
"""
Async Data Fetcher V6.0
========================
Enhanced with:
  - Circuit breaker pattern
  - Better error handling
  - Improved caching
  - Rate limit awareness
"""
import time
import asyncio
import logging
from typing import Optional, Dict
import numpy as np
import pandas as pd
from config import DataConfig

from api_layer import MarketDataFactory, OHLCVData

logger = logging.getLogger(__name__)


class _CacheEntry:
    __slots__ = ("data", "timestamp")

    def __init__(self, data, timestamp):
        self.data = data
        self.timestamp = timestamp


class DataFetcher:
    """Unified data fetcher with circuit breaker."""

    def __init__(self, cfg: Optional[DataConfig] = None):
        self.cfg = cfg or DataConfig()
        self._cache: Dict[str, _CacheEntry] = {}
        self._yf_provider = MarketDataFactory.create("yfinance")
        self._ccxt_provider = None
        self._failure_count: Dict[str, int] = {}  # Circuit breaker

    def _get_ccxt_provider(self, exchange_id: str = "binance"):
        if self._ccxt_provider is None:
            self._ccxt_provider = MarketDataFactory.create("ccxt", exchange_id=exchange_id)
        return self._ccxt_provider

    def _cache_key(self, source, a, b, period):
        return f"{source}:{a}:{b}:{period}"

    def _is_valid(self, entry):
        return (time.time() - entry.timestamp) < self.cfg.cache_ttl_seconds

    def _evict_if_needed(self):
        if len(self._cache) < 200:
            return
        # Evict oldest 10%
        oldest_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k].timestamp)[:len(self._cache)//10]
        for key in oldest_keys:
            del self._cache[key]

    def _check_circuit_breaker(self, key: str) -> bool:
        """Check if circuit breaker is open."""
        return self._failure_count.get(key, 0) >= self.cfg.circuit_breaker_threshold

    def _record_failure(self, key: str):
        """Record a failure for circuit breaker."""
        self._failure_count[key] = self._failure_count.get(key, 0) + 1

    def _record_success(self, key: str):
        """Record success and reset circuit breaker."""
        self._failure_count[key] = 0

    async def get_yfinance(self, ticker_a, ticker_b, period="2y") -> pd.DataFrame:
        """Get paired data from yfinance with circuit breaker."""
        key = self._cache_key("yf", ticker_a, ticker_b, period)
        
        # Check cache
        if key in self._cache and self._is_valid(self._cache[key]):
            return self._cache[key].data.copy()
        
        # Check circuit breaker
        if self._check_circuit_breaker(key):
            logger.warning(f"Circuit breaker open for {key} — using cached or default data")
            # Return cached data even if expired
            if key in self._cache:
                return self._cache[key].data.copy()
            return None

        for attempt in range(self.cfg.retry_max):
            try:
                df_a = await self._yf_provider.get_ohlcv(ticker_a, timeframe="1d")
                df_b = await self._yf_provider.get_ohlcv(ticker_b, timeframe="1d")
                df = self._align_pair_data(df_a, df_b)

                if df is not None and len(df) > 50:
                    self._cache[key] = _CacheEntry(df, time.time())
                    self._evict_if_needed()
                    self._record_success(key)
                    return df.copy()
                
                self._record_failure(key)

            except Exception as e:
                logger.warning(f"YFinance attempt {attempt+1}: {e}")
                self._record_failure(key)
                if attempt < self.cfg.retry_max - 1:
                    jitter = random.uniform(0.5, 1.5)  # Add jitter to prevent thundering herd
                await asyncio.sleep((self.cfg.retry_backoff ** attempt) * jitter)

        # Fallback to legacy method
        return await self._legacy_download(ticker_a, ticker_b, period)

    def _align_pair_data(self, data_a: OHLCVData, data_b: OHLCVData) -> Optional[pd.DataFrame]:
        """Align two OHLCV datasets by timestamp."""
        try:
            df_a = data_a.to_dataframe()
            df_b = data_b.to_dataframe()

            df_a = df_a[["close"]].rename(columns={"close": data_a.symbol})
            df_b = df_b[["close"]].rename(columns={"close": data_b.symbol})

            df = df_a.join(df_b, how="inner")

            if len(df) < 2:
                return None
            return df
        except Exception as e:
            logger.error(f"Align pair data error: {e}")
            return None

    @staticmethod
    def _legacy_download(ticker_a, ticker_b, period):
        """Legacy yfinance download (fallback) with timeout protection V10.0."""
        import yfinance as yf
        import threading
        tickers = [ticker_a, ticker_b]
        result = [None, None]  # [data, exception]

        def _download():
            try:
                result[0] = yf.download(tickers, period=period, progress=False, auto_adjust=False)
            except Exception as e:
                result[1] = e

        t = threading.Thread(target=_download)
        t.daemon = True
        t.start()
        t.join(timeout=30)

        if t.is_alive():
            logger.error(f"yfinance download timed out after 30s for {ticker_a}/{ticker_b}")
            return None

        if result[1]:
            logger.error(f"Legacy download failed: {result[1]}")
            return None

        raw = result[0]
        if raw is None or raw.empty:
            # Second attempt with Close instead of Adj Close
            t2 = threading.Thread(target=_download)
            t2.daemon = True
            t2.start()
            t2.join(timeout=30)
            if t2.is_alive():
                logger.error(f"yfinance fallback download timed out after 30s for {ticker_a}/{ticker_b}")
                return None
            raw = result[0]
            if raw is None or raw.empty:
                return None

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        price_col = "Adj Close" if "Adj Close" in raw.columns else "Close"
        df = raw[price_col].copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        available = [t for t in tickers if t in df.columns]
        if len(available) < 2:
            return None
        return df[available].dropna()

    async def get_ccxt(self, symbol_a, symbol_b, timeframe="1d",
                       limit=500, exchange_id="binance"):
        """Get paired crypto data using CCXT."""
        provider = self._get_ccxt_provider(exchange_id)
        loop = asyncio.get_running_loop()

        async def _fetch_pair():
            data_a = await provider.get_ohlcv(symbol_a, timeframe=timeframe, limit=limit)
            data_b = await provider.get_ohlcv(symbol_b, timeframe=timeframe, limit=limit)
            return self._align_pair_data(data_a, data_b)

        return await loop.create_task(_fetch_pair())

    def invalidate(self, key=None):
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()
            self._failure_count.clear()

    def get_cache_stats(self) -> dict:
        return {
            "cache_size": len(self._cache),
            "circuit_breakers": dict(self._failure_count),
        }
