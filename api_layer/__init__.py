"""
API Layer — Unified interface for market data and execution.
=============================================================
Providers:
  - yfinance: Yahoo Finance (equities, crypto via YF)
  - CCXT: Crypto exchanges (Binance, Coinbase, etc.)
  - Alpaca: US equities trading (paper & live)

Usage:
    from api_layer import MarketDataFactory, ExecutionFactory
    
    # Market data
    md = MarketDataFactory.create("yfinance")
    data = await md.get_ohlcv("SPY", "QQQ", period="2y")
    
    # Execution
    exec = ExecutionFactory.create("alpaca_paper")
    result = await exec.execute_order("SPY", "BUY", qty=10)
"""
from .base import MarketDataProvider, ExecutionProvider, OHLCVData, OrderResult, OrderSide
from .yfinance_api import YFinanceMarketData
from .ccxt_api import CCXTMarketData
from .alpaca_api import AlpacaExecution, AlpacaMarketData
from .factory import MarketDataFactory, ExecutionFactory

__all__ = [
    # Base interfaces
    "MarketDataProvider",
    "ExecutionProvider",
    "OHLCVData",
    "OrderResult",
    "OrderSide",
    # Implementations
    "YFinanceMarketData",
    "CCXTMarketData",
    "AlpacaExecution",
    "AlpacaMarketData",
    # Factories
    "MarketDataFactory",
    "ExecutionFactory",
]
