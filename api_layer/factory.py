"""
Factory classes for API providers.
===================================
Provides unified creation and configuration of market data and execution providers.
V6.1: Added Binance, Bybit, Futu, IBKR, Tiger Securities.
"""
from __future__ import annotations
import logging
from typing import Optional, Dict, Type
from .base import MarketDataProvider, ExecutionProvider
from .yfinance_api import YFinanceMarketData
from .ccxt_api import CCXTMarketData
from .alpaca_api import AlpacaExecution, AlpacaMarketData
from .binance_api import BinanceExecution, BinanceMarketData
from .bybit_api import BybitExecution, BybitMarketData
from .futu_api import FutuExecution, FutuMarketData
from .ibkr_api import IBKRExecution, IBKRMarketData
from .tiger_api import TigerExecution, TigerMarketData

logger = logging.getLogger(__name__)


class MarketDataFactory:
    """Factory for creating market data providers."""

    _providers: Dict[str, Type[MarketDataProvider]] = {
        "yfinance": YFinanceMarketData,
        "yahoo": YFinanceMarketData,
        "yf": YFinanceMarketData,
        "alpaca-data": AlpacaMarketData,
        "binance-data": BinanceMarketData,
        "bybit-data": BybitMarketData,
        "futu-data": FutuMarketData,
        "ibkr-data": IBKRMarketData,
        "tiger-data": TigerMarketData,
    }

    @classmethod
    def register(cls, name: str, provider_class: Type[MarketDataProvider]):
        """Register a custom provider."""
        cls._providers[name.lower()] = provider_class

    @classmethod
    def create(cls, provider: str, **kwargs) -> MarketDataProvider:
        provider_key = provider.lower()

        # Handle CCXT
        if provider_key.startswith("ccxt"):
            exchange_id = kwargs.get("exchange_id", "binance")
            return CCXTMarketData(exchange_id=exchange_id, **kwargs)

        if provider_key not in cls._providers:
            available = list(cls._providers.keys()) + ["ccxt:*"]
            raise ValueError(f"Unknown provider '{provider}'. Available: {available}")

        return cls._providers[provider_key](**kwargs)

    @classmethod
    def list_providers(cls) -> list:
        return list(cls._providers.keys()) + ["ccxt:*"]


class ExecutionFactory:
    """Factory for creating execution providers."""

    _providers: Dict[str, Type[ExecutionProvider]] = {
        "alpaca_paper": AlpacaExecution,
        "alpaca_live": AlpacaExecution,
        "alpaca": AlpacaExecution,
        "binance_testnet": BinanceExecution,
        "binance_live": BinanceExecution,
        "binance": BinanceExecution,
        "bybit_testnet": BybitExecution,
        "bybit_live": BybitExecution,
        "bybit": BybitExecution,
        "futu_paper": FutuExecution,
        "futu_live": FutuExecution,
        "futu": FutuExecution,
        "ibkr_paper": IBKRExecution,
        "ibkr_live": IBKRExecution,
        "ibkr": IBKRExecution,
        "tiger_paper": TigerExecution,
        "tiger_live": TigerExecution,
        "tiger": TigerExecution,
    }

    @classmethod
    def register(cls, name: str, provider_class: Type[ExecutionProvider]):
        cls._providers[name.lower()] = provider_class

    @classmethod
    def create(cls, provider: str, **kwargs) -> ExecutionProvider:
        provider_key = provider.lower()

        if provider_key not in cls._providers:
            available = list(cls._providers.keys())
            raise ValueError(f"Unknown provider '{provider}'. Available: {available}")

        provider_cls = cls._providers[provider_key]

        # Alpaca
        if provider_key.startswith("alpaca"):
            is_paper = "paper" in provider_key or provider_key == "alpaca"
            return provider_cls(paper=is_paper, **kwargs)

        # Binance
        if provider_key.startswith("binance"):
            is_testnet = "testnet" in provider_key or provider_key == "binance"
            return provider_cls(testnet=is_testnet, **kwargs)

        # Bybit
        if provider_key.startswith("bybit"):
            is_testnet = "testnet" in provider_key or provider_key == "bybit"
            return provider_cls(testnet=is_testnet, **kwargs)

        # Futu
        if provider_key.startswith("futu"):
            is_paper = "paper" in provider_key or provider_key == "futu"
            return provider_cls(paper_trading=is_paper, **kwargs)

        # IBKR
        if provider_key.startswith("ibkr"):
            is_paper = "paper" in provider_key or provider_key == "ibkr"
            return provider_cls(paper_trading=is_paper, **kwargs)

        # Tiger
        if provider_key.startswith("tiger"):
            is_paper = "paper" in provider_key or provider_key == "tiger"
            return provider_cls(paper_trading=is_paper, **kwargs)

        return provider_cls(**kwargs)

    @classmethod
    def list_providers(cls) -> list:
        return list(cls._providers.keys())


# Convenience functions
def get_market_data(provider: str = "yfinance", **kwargs) -> MarketDataProvider:
    """Get a market data provider."""
    return MarketDataFactory.create(provider, **kwargs)


def get_execution(provider: str = "alpaca_paper", **kwargs) -> ExecutionProvider:
    """Get an execution provider."""
    return ExecutionFactory.create(provider, **kwargs)
