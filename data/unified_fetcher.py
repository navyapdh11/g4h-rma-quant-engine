#!/usr/bin/env python3
"""
Unified Multi-Source Data Fetcher V9.0
========================================
Smart fallback chain:
  1. Cache (in-memory, 4h TTL)
  2. CCXT Crypto (Binance, Bybit — LIVE, works now!)
  3. Local CSV Historical Data (for equities/ADRs)
  4. yfinance (when network available)

Supports:
  - Equity pairs (AAPL/MSFT, JPM/BAC, etc.) → CSV fallback
  - Crypto pairs (BTC/ETH, SOL/AVAX, etc.) → CCXT live
  - Cross-asset pairs (BTC-USD/ETH-USD, etc.)
  - Aligned paired data with inner join
"""
from __future__ import annotations
import os
import sys
import time
import asyncio
import logging
from typing import Optional, Dict, List, Tuple
from collections import OrderedDict

import numpy as np
import pandas as pd

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import settings

logger = logging.getLogger("g4h_fetcher")


class CacheEntry:
    """Cache entry with timestamp and data."""
    __slots__ = ['data', 'timestamp']
    def __init__(self, data: pd.DataFrame, timestamp: float):
        self.data = data
        self.timestamp = timestamp


class UnifiedDataFetcher:
    """
    Multi-source data fetcher with smart fallback.

    Data sources (in priority order):
      1. In-memory cache (TTL: 4 hours)
      2. CCXT crypto exchanges (Binance, Bybit — live)
      3. Local CSV historical data (generated GBM data)
      4. yfinance (when network available)
    """

    # Known crypto symbols that work with CCXT
    CRYPTO_SYMBOLS = {
        "BTC": "BTC/USDT", "ETH": "ETH/USDT", "SOL": "SOL/USDT",
        "BNB": "BNB/USDT", "XRP": "XRP/USDT", "ADA": "ADA/USDT",
        "AVAX": "AVAX/USDT", "DOT": "DOT/USDT", "MATIC": "MATIC/USDT",
        "LINK": "LINK/USDT", "UNI": "UNI/USDT", "ATOM": "ATOM/USDT",
        "LTC": "LTC/USDT", "BCH": "BCH/USDT", "FIL": "FIL/USDT",
        "APT": "APT/USDT", "ARB": "ARB/USDT", "OP": "OP/USDT",
        "DOGE": "DOGE/USDT", "SHIB": "SHIB/USDT",
    }

    # CCXT exchange instances (lazy-loaded)
    _ccxt_exchanges: Dict[str, object] = {}
    _ccxt_available: Optional[bool] = None

    def __init__(self):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._cache_max_size = 200
        self._cache_ttl = settings.data.cache_ttl_seconds  # 4 hours default
        self._circuit_breaker: Dict[str, Tuple[int, float]] = {}
        self._circuit_threshold = 5
        self._circuit_timeout = 60  # seconds

        # Local data loader (lazy-loaded)
        self._local_loader = None

        # Historical data directory
        self._historical_dir = os.path.join(PROJECT_ROOT, "data", "historical")

    # ──────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────

    def get_yfinance(self, ticker_a: str, ticker_b: str,
                     period: str = "2y") -> Optional[pd.DataFrame]:
        """
        Get paired OHLCV data for two equity/ADR symbols.
        Fallback chain: Cache → Local CSV → yfinance → Generated
        """
        cache_key = f"eq:{ticker_a}_{ticker_b}_{period}"

        # 1. Check cache
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        # 2. Check circuit breaker
        if self._is_circuit_open(cache_key):
            logger.debug(f"Circuit breaker open for {cache_key}, using local data")
            return self._get_local_paired(ticker_a, ticker_b, period, cache_key)

        try:
            # 3. Try local CSV data first (fast, always available)
            df = self._get_local_paired(ticker_a, ticker_b, period, cache_key)
            if df is not None and len(df) >= 30:
                self._set_cache(cache_key, df)
                self._record_success(cache_key)
                return df

            # 4. Try yfinance as fallback
            df = self._try_yfinance(ticker_a, ticker_b, period)
            if df is not None and len(df) >= 30:
                self._set_cache(cache_key, df)
                self._record_success(cache_key)
                return df

            # 5. Generate data as last resort
            df = self._generate_and_save(ticker_a, ticker_b, period, cache_key)
            if df is not None:
                self._set_cache(cache_key, df)
                return df

        except Exception as e:
            logger.warning(f"Equity fetch failed for {ticker_a}/{ticker_b}: {e}")
            self._record_failure(cache_key)

        return None

    def get_ccxt(self, symbol_a: str, symbol_b: str,
                 timeframe: str = "1d", limit: int = 500,
                 exchange_id: str = "binance") -> Optional[pd.DataFrame]:
        """
        Get paired crypto data from CCXT exchanges.
        Works LIVE — Binance and Bybit are accessible.
        """
        cache_key = f"ccxt:{symbol_a}_{symbol_b}_{exchange_id}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        try:
            exchange = self._get_ccxt_exchange(exchange_id)
            if exchange is None:
                return None

            # Fetch OHLCV for both symbols
            ohlcv_a = self._fetch_ccxt_ohlcv(exchange, symbol_a, timeframe, limit)
            ohlcv_b = self._fetch_ccxt_ohlcv(exchange, symbol_b, timeframe, limit)

            if ohlcv_a is None or ohlcv_b is None:
                return None

            # Align by timestamp
            df = self._align_crypto_data(ohlcv_a, ohlcv_b, symbol_a, symbol_b)
            if df is not None and len(df) >= 30:
                self._set_cache(cache_key, df)
                return df

        except Exception as e:
            logger.warning(f"CCXT fetch failed for {symbol_a}/{symbol_b}: {e}")

        return None

    def get_crypto_pair(self, pair: str, timeframe: str = "1d",
                        limit: int = 500) -> Optional[pd.DataFrame]:
        """
        Get data for a crypto pair like 'BTC/ETH' or 'BTC-USD/ETH-USD'.
        Normalizes pair format and fetches from Binance.
        """
        # Normalize pair format
        pair = pair.upper().replace("-", "/")
        if "/" not in pair:
            parts = pair.split("_")
            if len(parts) == 2:
                pair = f"{parts[0]}/{parts[1]}"
            else:
                return None

        # Extract base and quote from pair (e.g., BTC/ETH → BTC, ETH)
        base_quote = pair.split("/")
        if len(base_quote) != 2:
            return None

        # For pair scanning, we need a common quote (USDT)
        base_a = base_quote[0]
        # If it's a direct pair like BTC/ETH, treat as BTC/USDT and ETH/USDT
        if len(base_quote) == 2:
            symbol_a = f"{base_a}/USDT"
            symbol_b = f"{base_quote[1]}/USDT"
        else:
            return None

        return self.get_ccxt(base_a, base_quote[1], timeframe, limit, "binance")

    def clear_cache(self):
        """Clear all cached data."""
        self._cache.clear()

    def get_cache_stats(self) -> Dict:
        """Get cache statistics."""
        return {
            "size": len(self._cache),
            "max_size": self._cache_max_size,
            "ttl_seconds": self._cache_ttl,
            "circuit_breakers": {k: {"failures": v[0], "since": v[1]}
                                 for k, v in self._circuit_breaker.items()},
            "ccxt_available": self._check_ccxt_available(),
        }

    # ──────────────────────────────────────────────────────
    # CCXT CRYPTO
    # ──────────────────────────────────────────────────────

    def _check_ccxt_available(self) -> bool:
        """Check if CCXT is available and exchanges are reachable."""
        if self._ccxt_available is not None:
            return self._ccxt_available
        try:
            import ccxt
            self._ccxt_available = True
            return True
        except ImportError:
            self._ccxt_available = False
            return False

    def _get_ccxt_exchange(self, exchange_id: str = "binance"):
        """Get or create a CCXT exchange instance."""
        if exchange_id in self._ccxt_exchanges:
            return self._ccxt_exchanges[exchange_id]

        try:
            import ccxt
            config = {
                'enableRateLimit': True,
                'timeout': 10000,  # 10 seconds
            }
            exchange_class = getattr(ccxt, exchange_id)
            exchange = exchange_class(config)
            self._ccxt_exchanges[exchange_id] = exchange
            logger.info(f"CCXT {exchange_id} initialized")
            return exchange
        except Exception as e:
            logger.error(f"CCXT {exchange_id} init failed: {e}")
            return None

    def _fetch_ccxt_ohlcv(self, exchange, symbol: str,
                          timeframe: str, limit: int) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data from a CCXT exchange."""
        try:
            # Normalize symbol
            if not symbol.endswith("/USDT"):
                symbol = f"{symbol}/USDT"

            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not ohlcv:
                return None

            df = pd.DataFrame(ohlcv, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            df.index = pd.to_datetime(df.index)
            return df
        except Exception as e:
            logger.debug(f"CCXT OHLCV fetch failed for {symbol}: {e}")
            return None

    def _align_crypto_data(self, df_a: pd.DataFrame, df_b: pd.DataFrame,
                           sym_a: str, sym_b: str) -> Optional[pd.DataFrame]:
        """Align two crypto datasets by timestamp."""
        # Extract close prices
        pa = df_a[["Close"]].rename(columns={"Close": sym_a})
        pb = df_b[["Close"]].rename(columns={"Close": sym_b})

        # Inner join on timestamp
        aligned = pa.join(pb, how="inner")

        # Resample to daily if needed
        if len(aligned) > 0:
            aligned = aligned.resample("1D").last().dropna()

        if len(aligned) < 30:
            return None

        return aligned

    # ──────────────────────────────────────────────────────
    # LOCAL CSV DATA
    # ──────────────────────────────────────────────────────

    def _get_local_loader(self):
        """Lazy-load the local data module."""
        if self._local_loader is None:
            try:
                from data.local_data import generate_symbol_data, SYMBOL_PARAMS
                self._local_loader = {
                    "generate": generate_symbol_data,
                    "params": SYMBOL_PARAMS,
                }
            except ImportError as e:
                logger.error(f"Local data loader not available: {e}")
                self._local_loader = False
        return self._local_loader if self._local_loader else None

    def _get_local_paired(self, sym_a: str, sym_b: str,
                          period: str, cache_key: str) -> Optional[pd.DataFrame]:
        """Get paired data from local CSV or generated data."""
        loader = self._get_local_loader()
        if loader is None:
            return None

        try:
            # Try loading existing CSV files
            csv_a = os.path.join(self._historical_dir, f"{sym_a}_{period}.csv")
            csv_b = os.path.join(self._historical_dir, f"{sym_b}_{period}.csv")

            df_a = None
            df_b = None

            if os.path.exists(csv_a):
                df_a = pd.read_csv(csv_a, index_col=0, parse_dates=True)
            if os.path.exists(csv_b):
                df_b = pd.read_csv(csv_b, index_col=0, parse_dates=True)

            # Generate if not available
            if df_a is None:
                df_a = loader["generate"](sym_a, period, self._historical_dir)
            if df_b is None:
                df_b = loader["generate"](sym_b, period, self._historical_dir)

            if df_a is None or df_b is None:
                return None

            # Align
            pa = df_a[["Close"]].rename(columns={"Close": sym_a})
            pb = df_b[["Close"]].rename(columns={"Close": sym_b})
            aligned = pa.join(pb, how="inner")

            if len(aligned) < 30:
                return None

            return aligned

        except Exception as e:
            logger.debug(f"Local data failed for {sym_a}/{sym_b}: {e}")
            return None

    def _generate_and_save(self, sym_a: str, sym_b: str,
                           period: str, cache_key: str) -> Optional[pd.DataFrame]:
        """Generate data for both symbols and save."""
        loader = self._get_local_loader()
        if loader is None:
            return None

        try:
            df_a = loader["generate"](sym_a, period, self._historical_dir)
            df_b = loader["generate"](sym_b, period, self._historical_dir)
            if df_a is None or df_b is None:
                return None

            pa = df_a[["Close"]].rename(columns={"Close": sym_a})
            pb = df_b[["Close"]].rename(columns={"Close": sym_b})
            aligned = pa.join(pb, how="inner")

            if len(aligned) >= 30:
                self._set_cache(cache_key, aligned)
                return aligned
        except Exception as e:
            logger.debug(f"Generate failed: {e}")
        return None

    # ──────────────────────────────────────────────────────
    # YFINANCE FALLBACK
    # ──────────────────────────────────────────────────────

    def _try_yfinance(self, ticker_a: str, ticker_b: str,
                      period: str) -> Optional[pd.DataFrame]:
        """Try fetching from yfinance with timeout protection."""
        try:
            import yfinance as yf
            # Quick check: try fetching with very short timeout
            import requests
            session = requests.Session()
            session.get("https://query1.finance.yahoo.com", timeout=3)

            # If we get here, yfinance might work
            data = yf.download(
                f"{ticker_a} {ticker_b}",
                period=period,
                progress=False,
                auto_adjust=False,
            )
            if data is None or len(data) < 30:
                return None

            # Flatten multi-index columns
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            return data

        except Exception:
            return None

    # ──────────────────────────────────────────────────────
    # CACHE
    # ──────────────────────────────────────────────────────

    def _get_cache(self, key: str) -> Optional[pd.DataFrame]:
        """Get data from cache if not expired."""
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry.timestamp < self._cache_ttl:
                # Move to end (LRU)
                self._cache.move_to_end(key)
                return entry.data
            else:
                del self._cache[key]
        return None

    def _set_cache(self, key: str, data: pd.DataFrame):
        """Add data to cache, evicting oldest if full."""
        if len(self._cache) >= self._cache_max_size:
            # Evict oldest 10%
            n_evict = max(1, self._cache_max_size // 10)
            for _ in range(n_evict):
                self._cache.popitem(last=False)
        self._cache[key] = CacheEntry(data, time.time())

    # ──────────────────────────────────────────────────────
    # CIRCUIT BREAKER
    # ──────────────────────────────────────────────────────

    def _is_circuit_open(self, key: str) -> bool:
        """Check if circuit breaker is open (too many failures)."""
        if key in self._circuit_breaker:
            failures, last_failure_time = self._circuit_breaker[key]
            if failures >= self._circuit_threshold:
                if time.time() - last_failure_time < self._circuit_timeout:
                    return True
                else:
                    # Reset after timeout
                    del self._circuit_breaker[key]
        return False

    def _record_failure(self, key: str):
        """Record a fetch failure."""
        if key in self._circuit_breaker:
            failures, _ = self._circuit_breaker[key]
            self._circuit_breaker[key] = (failures + 1, time.time())
        else:
            self._circuit_breaker[key] = (1, time.time())

    def _record_success(self, key: str):
        """Record a fetch success, reset circuit breaker."""
        if key in self._circuit_breaker:
            del self._circuit_breaker[key]


# Create singleton instance
fetcher = UnifiedDataFetcher()
