"""
Base API interfaces — Abstract base classes for all providers.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
import pandas as pd
import numpy as np


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


@dataclass
class OHLCVData:
    """Standardized OHLCV data structure."""
    symbol: str
    timestamp: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    
    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, symbol: str) -> "OHLCVData":
        """Create from DataFrame with OHLCV columns."""
        return cls(
            symbol=symbol,
            timestamp=df.index,
            open=df["open"].values if "open" in df else df["Open"].values,
            high=df["high"].values if "high" in df else df["High"].values,
            low=df["low"].values if "low" in df else df["Low"].values,
            close=df["close"].values if "close" in df else df["Close"].values,
            volume=df["volume"].values if "volume" in df else df["Volume"].values,
        )
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert to DataFrame."""
        return pd.DataFrame(
            {
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
            },
            index=self.timestamp,
        )
    
    @property
    def vwap(self) -> np.ndarray:
        """Calculate Volume Weighted Average Price."""
        typical_price = (self.high + self.low + self.close) / 3
        return np.cumsum(typical_price * self.volume) / np.cumsum(self.volume)
    
    @property
    def returns(self) -> np.ndarray:
        """Calculate log returns."""
        return np.diff(np.log(self.close))


@dataclass
class OrderResult:
    """Standardized order execution result."""
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    filled_qty: float
    avg_price: float
    status: OrderStatus
    timestamp: datetime
    commission: float = 0.0
    raw_response: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    
    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED
    
    @property
    def notional_value(self) -> float:
        return self.filled_qty * self.avg_price
    
    @property
    def total_cost(self) -> float:
        return self.notional_value + self.commission


@dataclass
class Position:
    """Current position in a symbol."""
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    
    @property
    def is_long(self) -> bool:
        return self.qty > 0
    
    @property
    def is_short(self) -> bool:
        return self.qty < 0


@dataclass
class Ticker:
    """Real-time ticker data."""
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float
    timestamp: datetime


class MarketDataProvider(ABC):
    """Abstract base class for market data providers."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'yfinance', 'ccxt', 'alpaca')."""
        pass
    
    @property
    @abstractmethod
    def supported_assets(self) -> List[str]:
        """List of supported asset classes (e.g., 'equity', 'crypto')."""
        pass
    
    @abstractmethod
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> OHLCVData:
        """
        Get OHLCV data for a symbol.
        
        Args:
            symbol: Ticker symbol (e.g., 'SPY', 'BTC/USDT')
            timeframe: Bar interval (e.g., '1m', '5m', '1h', '1d')
            start: Start date (provider-dependent if None)
            end: End date (now if None)
            limit: Maximum bars to return
            
        Returns:
            OHLCVData object with standardized data
        """
        pass
    
    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker:
        """Get real-time ticker for a symbol."""
        pass
    
    @abstractmethod
    async def get_symbols(self, asset_class: Optional[str] = None) -> List[str]:
        """Get list of available symbols."""
        pass
    
    @abstractmethod
    async def is_available(self) -> bool:
        """Check if provider is available and healthy."""
        pass


class ExecutionProvider(ABC):
    """Abstract base class for execution providers."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""
        pass
    
    @property
    @abstractmethod
    def is_paper(self) -> bool:
        """True if paper/simulated trading."""
        pass
    
    @abstractmethod
    async def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: str = "gtc",
        dry_run: bool = False,
    ) -> OrderResult:
        """
        Submit an order.
        
        Args:
            symbol: Ticker symbol
            side: BUY or SELL
            qty: Order quantity
            order_type: Market, Limit, Stop, or Stop-Limit
            limit_price: Limit price (for limit orders)
            stop_price: Stop price (for stop orders)
            time_in_force: GTC, DAY, IOC, FOK
            dry_run: If True, simulate without submitting
            
        Returns:
            OrderResult with execution details
        """
        pass
    
    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        pass
    
    @abstractmethod
    async def get_order(self, order_id: str) -> OrderResult:
        """Get order status."""
        pass
    
    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Position]:
        """Get current position for a symbol."""
        pass
    
    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """Get all open positions."""
        pass
    
    @abstractmethod
    async def get_account(self) -> Dict[str, Any]:
        """Get account information (balance, buying power, etc.)."""
        pass
    
    @abstractmethod
    async def is_available(self) -> bool:
        """Check if provider is available and healthy."""
        pass
