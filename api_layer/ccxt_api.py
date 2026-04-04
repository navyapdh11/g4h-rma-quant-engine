"""
CCXT Crypto Exchange API Wrapper.
==================================
Unified interface for 100+ cryptocurrency exchanges.
Features:
  - Real-time and historical OHLCV data
  - Multiple exchanges (Binance, Coinbase, Kraken, etc.)
  - Rate limit handling
  - Automatic retry with exponential backoff
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import pandas as pd
import numpy as np

from .base import (
    MarketDataProvider,
    OHLCVData,
    Ticker,
)

logger = logging.getLogger(__name__)


class CCXTMarketData(MarketDataProvider):
    """CCXT cryptocurrency market data provider."""
    
    # Timeframe mapping for CCXT (in milliseconds)
    TIMEFRAME_MAP = {
        "1m": 60000,
        "3m": 180000,
        "5m": 300000,
        "15m": 900000,
        "30m": 1800000,
        "1h": 3600000,
        "2h": 7200000,
        "4h": 14400000,
        "6h": 21600000,
        "12h": 43200000,
        "1d": 86400000,
        "3d": 259200000,
        "1w": 604800000,
        "2w": 1209600000,
        "1mo": 2592000000,
    }
    
    # CCXT timeframe strings
    CCXT_TF_MAP = {
        "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h",
        "1d": "1d", "3d": "3d", "1w": "1w", "2w": "2w", "1mo": "1M",
    }
    
    def __init__(
        self,
        exchange_id: str = "binance",
        rate_limit: bool = True,
        retry_max: int = 5,
        retry_delay: float = 1.0,
    ):
        self.exchange_id = exchange_id
        self.rate_limit = rate_limit
        self.retry_max = retry_max
        self.retry_delay = retry_delay
        self._exchange = None
        self._cache: Dict[str, tuple] = {}
        self._cache_ttl = 60  # 1 minute for crypto
    
    @property
    def name(self) -> str:
        return f"ccxt:{self.exchange_id}"
    
    @property
    def supported_assets(self) -> List[str]:
        return ["crypto"]
    
    def _get_exchange(self):
        """Lazy-load exchange instance."""
        if self._exchange is None:
            import ccxt
            exchange_class = getattr(ccxt, self.exchange_id)
            self._exchange = exchange_class({
                "enableRateLimit": self.rate_limit,
                "timeout": 30000,
                "options": {"defaultType": "spot"},
            })
        return self._exchange
    
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> OHLCVData:
        """
        Get OHLCV data from CCXT exchange.
        
        Note: CCXT returns most recent data first when using 'since'.
        """
        exchange = self._get_exchange()
        ccxt_tf = self.CCXT_TF_MAP.get(timeframe, "1d")
        
        # Normalize symbol format (e.g., "BTC/USDT")
        if "/" not in symbol:
            # Try to find the symbol on the exchange
            await self._load_markets()
            for s in exchange.symbols:
                if symbol in s:
                    symbol = s
                    break
        
        cache_key = f"{symbol}_{timeframe}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key][0]
        
        for attempt in range(self.retry_max):
            try:
                loop = asyncio.get_running_loop()
                
                if start:
                    since = int(start.timestamp() * 1000)
                else:
                    since = None
                
                # CCXT limit defaults to 500
                ccxt_limit = limit or 500
                
                bars = await loop.run_in_executor(
                    None,
                    lambda: exchange.fetch_ohlcv(symbol, timeframe=ccxt_tf, since=since, limit=ccxt_limit),
                )
                
                if not bars:
                    raise ValueError(f"No data returned for {symbol}")
                
                # Convert to DataFrame
                df = pd.DataFrame(
                    bars,
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                df = df.set_index("timestamp")
                df = df.sort_index()
                
                ohlcv = OHLCVData.from_dataframe(df, symbol)
                
                # Cache
                self._cache[cache_key] = (ohlcv, datetime.now())
                
                return ohlcv
                
            except Exception as e:
                logger.warning(f"CCXT {self.exchange_id} attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_max - 1:
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                else:
                    raise RuntimeError(f"Failed to fetch {symbol} from {self.exchange_id}")
        
        raise RuntimeError("Unexpected error in get_ohlcv")
    
    async def _load_markets(self):
        """Load exchange markets."""
        exchange = self._get_exchange()
        if not exchange.symbols:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, exchange.load_markets)
    
    async def get_ticker(self, symbol: str) -> Ticker:
        """Get real-time ticker."""
        exchange = self._get_exchange()
        
        # Normalize symbol
        if "/" not in symbol:
            await self._load_markets()
            for s in exchange.symbols:
                if symbol in s:
                    symbol = s
                    break
        
        loop = asyncio.get_running_loop()
        
        def _fetch():
            return exchange.fetch_ticker(symbol)
        
        for attempt in range(self.retry_max):
            try:
                ticker_data = await loop.run_in_executor(None, _fetch)
                return Ticker(
                    symbol=symbol,
                    bid=ticker_data.get("bid", 0),
                    ask=ticker_data.get("ask", 0),
                    last=ticker_data.get("last", 0),
                    volume=ticker_data.get("baseVolume", 0),
                    timestamp=datetime.fromtimestamp(ticker_data["timestamp"] / 1000),
                )
            except Exception as e:
                logger.warning(f"Ticker fetch attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_max - 1:
                    await asyncio.sleep(self.retry_delay)
                else:
                    raise
        
        raise RuntimeError("Failed to fetch ticker")
    
    async def get_symbols(self, asset_class: Optional[str] = None) -> List[str]:
        """Get all available trading pairs on the exchange."""
        await self._load_markets()
        exchange = self._get_exchange()
        
        if asset_class == "crypto":
            # Filter for USDT pairs (most common)
            return [s for s in exchange.symbols if "USDT" in s]
        
        return exchange.symbols
    
    async def is_available(self) -> bool:
        """Check if exchange is reachable."""
        try:
            exchange = self._get_exchange()
            loop = asyncio.get_running_loop()
            # Fetch a single candle as health check
            await loop.run_in_executor(
                None,
                lambda: exchange.fetch_ohlcv("BTC/USDT", timeframe="1d", limit=1),
            )
            return True
        except Exception as e:
            logger.error(f"CCXT health check failed: {e}")
            return False
    
    def _is_cache_valid(self, key: str) -> bool:
        if key not in self._cache:
            return False
        _, timestamp = self._cache[key]
        return (datetime.now() - timestamp).total_seconds() < self._cache_ttl
    
    async def close(self):
        """Close exchange connection."""
        if self._exchange:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._exchange.close)
            self._exchange = None
