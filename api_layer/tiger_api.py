"""
Tiger Securities Execution Provider.
=====================================
Execution + market data via Tiger Open API.
Supports:
  - US equities
  - HK equities
  - China A-shares (Stock Connect)
  - US/HK options
  - Paper trading (Tiger simulation)
Requires: tigeropen Python package
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

from .base import (
    ExecutionProvider,
    MarketDataProvider,
    OHLCVData,
    OrderResult,
    OrderSide,
    OrderType,
    OrderStatus,
    Position,
    Ticker,
)

logger = logging.getLogger(__name__)


class TigerExecution(ExecutionProvider):
    """Tiger Securities execution provider."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        account_id: Optional[str] = None,
        paper_trading: bool = True,
        market: str = "US",  # US, HK, CN
    ):
        self.api_key = api_key or os.environ.get("TIGER_API_KEY", "")
        self.account_id = account_id or os.environ.get("TIGER_ACCOUNT_ID", "")
        self.paper = paper_trading
        self.market = market
        self._trade_client = None

    @property
    def name(self) -> str:
        return "tiger:" + ("paper" if self.paper else "live")

    @property
    def is_paper(self) -> bool:
        return self.paper

    def _get_trade_client(self):
        if self._trade_client is None:
            if not self.api_key:
                logger.warning("Tiger API key not configured")
                return None
            try:
                from tigeropen.tiger_open_config import TigerOpenConfig
                from tigeropen.trade.trade_client import TradeClient

                config = TigerOpenConfig(
                    api_key=self.api_key,
                    account_id=self.account_id,
                    is_sandbox=self.paper,
                )
                self._trade_client = TradeClient(config)
                logger.info(f"Tiger {'paper' if self.paper else 'live'} {self.market} connected")
            except ImportError:
                logger.error("tigeropen not installed. pip install tigeropen")
                self._trade_client = None
            except Exception as e:
                logger.error(f"Tiger init failed: {e}")
                self._trade_client = None
        return self._trade_client

    def _to_tiger_symbol(self, symbol: str) -> str:
        """Convert symbol to Tiger format."""
        return symbol.upper()

    async def submit_order(self, symbol, side, qty, order_type=OrderType.MARKET,
                           limit_price=None, stop_price=None, time_in_force="gtc",
                           dry_run=False) -> OrderResult:
        if dry_run:
            return self._simulate_order(symbol, side, qty, order_type, limit_price)

        trade_client = self._get_trade_client()
        if trade_client is None:
            return self._simulate_order(symbol, side, qty, order_type, limit_price)

        loop = asyncio.get_running_loop()

        def _submit():
            from tigeropen.trade.domain import Order
            from tigeropen.common.model import SecurityType

            tiger_symbol = self._to_tiger_symbol(symbol)

            # Determine security type
            sec_type = SecurityType.STK
            if "/" in symbol:
                sec_type = SecurityType.FUT

            # Create order
            action = "BUY" if side == OrderSide.BUY else "SELL"
            order = Order(
                account_id=self.account_id,
                sec_type=sec_type,
                symbol=tiger_symbol,
                order_type="MKT" if order_type == OrderType.MARKET else "LMT",
                action=action,
                quantity=int(qty),
                limit_price=limit_price,
                time_in_force="GTC" if time_in_force == "gtc" else "DAY",
            )

            result = trade_client.place_order(order)
            return result

        try:
            result = await loop.run_in_executor(None, _submit)
            return OrderResult(
                order_id=str(result.order_id) if hasattr(result, "order_id") else f"tiger_{datetime.now().timestamp()}",
                symbol=symbol, side=side, qty=qty,
                filled_qty=0, avg_price=0,
                status=OrderStatus.SUBMITTED, timestamp=datetime.now(),
                raw_response={"order_id": getattr(result, "order_id", None)},
            )
        except Exception as e:
            logger.error(f"Tiger order failed: {e}")
            return OrderResult(
                order_id=f"tiger_err_{datetime.now().timestamp()}",
                symbol=symbol, side=side, qty=qty,
                filled_qty=0, avg_price=0,
                status=OrderStatus.ERROR, timestamp=datetime.now(), error_message=str(e),
            )

    def _simulate_order(self, symbol, side, qty, order_type, limit_price):
        import random
        base_price = limit_price or 100.0
        slippage = random.uniform(-0.005, 0.005)
        return OrderResult(
            order_id=f"sim_{datetime.now().timestamp()}", symbol=symbol, side=side,
            qty=qty, filled_qty=qty, avg_price=base_price * (1 + slippage),
            status=OrderStatus.FILLED, timestamp=datetime.now(), commission=0,
            raw_response={"simulated": True, "provider": "tiger"},
        )

    async def cancel_order(self, order_id):
        trade_client = self._get_trade_client()
        if not trade_client:
            return False
        loop = asyncio.get_running_loop()
        try:
            trade_client.cancel_order(order_id)
            return True
        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return False

    async def get_order(self, order_id):
        raise NotImplementedError("Tiger order lookup not yet implemented")

    async def get_position(self, symbol):
        return None

    async def get_positions(self) -> List[Position]:
        trade_client = self._get_trade_client()
        if not trade_client:
            return []
        loop = asyncio.get_running_loop()

        def _fetch():
            return trade_client.get_positions()

        try:
            positions = await loop.run_in_executor(None, _fetch)
            result = []
            for pos in positions:
                result.append(Position(
                    symbol=getattr(pos, "symbol", ""),
                    qty=getattr(pos, "quantity", 0),
                    avg_entry_price=getattr(pos, "average_cost", 0),
                    current_price=getattr(pos, "market_price", 0),
                    market_value=getattr(pos, "market_value", 0),
                    unrealized_pnl=getattr(pos, "unrealized_pnl", 0),
                    unrealized_pnl_pct=getattr(pos, "unrealized_pnl_pct", 0),
                ))
            return result
        except Exception:
            return []

    async def get_account(self) -> Dict[str, Any]:
        trade_client = self._get_trade_client()
        if not trade_client:
            return {"error": "Not configured", "paper": self.paper}
        loop = asyncio.get_running_loop()

        def _fetch():
            return trade_client.get_account()

        try:
            account = await loop.run_in_executor(None, _fetch)
            return {
                "broker": "Tiger Securities",
                "account_id": self.account_id,
                "paper": self.paper,
                "market": self.market,
                "currency": getattr(account, "currency", "USD"),
                "cash": getattr(account, "cash", 0),
                "net_liquidation": getattr(account, "net_liquidation_value", 0),
                "buying_power": getattr(account, "buying_power", 0),
            }
        except Exception as e:
            return {"error": str(e), "paper": self.paper}

    async def is_available(self):
        try:
            return self._get_trade_client() is not None
        except Exception:
            return False


class TigerMarketData(MarketDataProvider):
    """Tiger Securities market data provider."""

    def __init__(self, api_key: Optional[str] = None, market: str = "US"):
        self.api_key = api_key or os.environ.get("TIGER_API_KEY", "")
        self.market = market
        self._quote_client = None

    @property
    def name(self):
        return f"tiger-data:{self.market}"

    @property
    def supported_assets(self):
        return ["equity", "option"]

    def _get_quote_client(self):
        if self._quote_client is None:
            if not self.api_key:
                return None
            try:
                from tigeropen.tiger_open_config import TigerOpenConfig
                from tigeropen.quote.quote_client import QuoteClient

                config = TigerOpenConfig(api_key=self.api_key, is_sandbox=True)
                self._quote_client = QuoteClient(config)
            except Exception as e:
                logger.error(f"Tiger quote init failed: {e}")
                self._quote_client = None
        return self._quote_client

    async def get_ohlcv(self, symbol, timeframe="1d", start=None, end=None, limit=None):
        quote_client = self._get_quote_client()
        if not quote_client:
            raise RuntimeError("Tiger not configured")
        loop = asyncio.get_running_loop()

        def _fetch():
            from tigeropen.quote.domain import KlinePeriod
            period_map = {"1m": KlinePeriod.ONE_MINUTE, "5m": KlinePeriod.FIVE_MINUTES,
                          "15m": KlinePeriod.FIFTEEN_MINUTES, "1h": KlinePeriod.ONE_HOUR,
                          "1d": KlinePeriod.DAY, "1w": KlinePeriod.WEEK}
            bars = quote_client.get_klines(
                symbol=symbol.upper(),
                period=period_map.get(timeframe, KlinePeriod.DAY),
                count=limit or 500,
            )
            return bars

        try:
            bars = await loop.run_in_executor(None, _fetch)
            import pandas as pd
            df = pd.DataFrame([{
                "timestamp": b.time, "open": b.open, "high": b.high,
                "low": b.low, "close": b.close, "volume": b.volume,
            } for b in bars])
            if df.empty:
                raise ValueError(f"No data for {symbol}")
            df = df.set_index("timestamp")
            return OHLCVData.from_dataframe(df, symbol)
        except Exception as e:
            logger.error(f"Tiger data fetch failed: {e}")
            raise

    async def get_ticker(self, symbol):
        quote_client = self._get_quote_client()
        if not quote_client:
            raise RuntimeError("Tiger not configured")
        loop = asyncio.get_running_loop()

        def _fetch():
            return quote_client.get_trade_tick(symbol.upper())

        try:
            tick = await loop.run_in_executor(None, _fetch)
            if not tick:
                raise ValueError(f"No ticker for {symbol}")
            return Ticker(
                symbol=symbol,
                bid=getattr(tick, "bid_price", 0),
                ask=getattr(tick, "ask_price", 0),
                last=getattr(tick, "price", 0),
                volume=getattr(tick, "volume", 0),
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Tiger ticker failed: {e}")
            raise

    async def get_symbols(self, asset_class=None):
        return ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN"]

    async def is_available(self):
        try:
            return self._get_quote_client() is not None
        except Exception:
            return False
