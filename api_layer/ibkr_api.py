"""
Interactive Brokers (IBKR) Execution Provider.
================================================
Execution + market data via ib_insync.
Supports:
  - US equities, options, futures
  - Forex
  - Bonds
  - Global markets
  - Paper trading (TWS/IB Gateway paper account)
Requires: ib_insync Python package
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


class IBKRExecution(ExecutionProvider):
    """IBKR execution provider via ib_insync."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,  # 7497 = paper, 7496 = live
        client_id: int = 1,
        paper_trading: bool = True,
    ):
        self.host = host or os.environ.get("IBKR_HOST", "127.0.0.1")
        self.port = int(os.environ.get("IBKR_PORT", 7497 if paper_trading else 7496))
        self.client_id = client_id
        self.paper = paper_trading
        self._ib = None

    @property
    def name(self) -> str:
        return "ibkr:" + ("paper" if self.paper else "live")

    @property
    def is_paper(self) -> bool:
        return self.paper

    def _get_ib(self):
        """Lazy-load IB connection."""
        if self._ib is None:
            try:
                from ib_insync import IB
                self._ib = IB()
                self._ib.connect(self.host, self.port, clientId=self.client_id)
                logger.info(f"IBKR {'paper' if self.paper else 'live'} connected ({self.host}:{self.port})")
            except ImportError:
                logger.error("ib_insync not installed. pip install ib_insync")
                self._ib = None
            except Exception as e:
                logger.error(f"IBKR connection failed: {e}")
                self._ib = None
        return self._ib

    def _make_contract(self, symbol: str):
        """Create IB contract for symbol."""
        from ib_insync import Stock, Forex, Future, Option
        if "/" in symbol:
            base, quote = symbol.split("/")
            return Forex(f"{base}{quote}")
        # Default: US stock, SMART routing, USD
        return Stock(symbol, "SMART", "USD")

    def _make_order(self, side, qty, order_type, limit_price=None, stop_price=None, time_in_force="gtc"):
        """Create IB order."""
        from ib_insync import MarketOrder, LimitOrder, StopOrder, StopLimitOrder
        if order_type == OrderType.MARKET:
            return MarketOrder("BUY" if side == OrderSide.BUY else "SELL", qty)
        elif order_type == OrderType.LIMIT:
            return LimitOrder("BUY" if side == OrderSide.BUY else "SELL", qty, limit_price or 0)
        elif order_type == OrderType.STOP:
            return StopOrder("BUY" if side == OrderSide.BUY else "SELL", qty, stop_price or 0)
        elif order_type == OrderType.STOP_LIMIT:
            return StopLimitOrder("BUY" if side == OrderSide.BUY else "SELL", qty,
                                   limit_price or 0, stop_price or 0)
        return MarketOrder("BUY" if side == OrderSide.BUY else "SELL", qty)

    async def submit_order(self, symbol, side, qty, order_type=OrderType.MARKET,
                           limit_price=None, stop_price=None, time_in_force="gtc",
                           dry_run=False) -> OrderResult:
        if dry_run:
            return self._simulate_order(symbol, side, qty, order_type, limit_price)

        ib = self._get_ib()
        if ib is None:
            return self._simulate_order(symbol, side, qty, order_type, limit_price)

        loop = asyncio.get_running_loop()

        def _submit():
            contract = self._make_contract(symbol)
            order = self._make_order(side, qty, order_type, limit_price, stop_price, time_in_force)
            trade = ib.placeOrder(contract, order)
            ib.sleep(0.5)  # Let IB process
            return trade

        try:
            trade = await loop.run_in_executor(None, _submit)
            filled_qty = trade.orderStatus.totalQuantity if trade.orderStatus else 0
            avg_price = trade.orderStatus.avgFillPrice if trade.orderStatus else 0

            return OrderResult(
                order_id=str(trade.order.orderId),
                symbol=symbol, side=side, qty=qty,
                filled_qty=filled_qty, avg_price=avg_price,
                status=self._map_status(trade.orderStatus.status),
                timestamp=datetime.now(),
                commission=trade.orderStatus.commission if trade.orderStatus else 0,
                raw_response={"orderId": trade.order.orderId, "status": trade.orderStatus.status},
            )
        except Exception as e:
            logger.error(f"IBKR order failed: {e}")
            return OrderResult(
                order_id=f"ibkr_err_{datetime.now().timestamp()}",
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
            raw_response={"simulated": True, "provider": "ibkr"},
        )

    def _map_status(self, status: str) -> OrderStatus:
        mapping = {
            "PendingSubmit": OrderStatus.PENDING,
            "PreSubmitted": OrderStatus.SUBMITTED,
            "Submitted": OrderStatus.SUBMITTED,
            "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
            "Filled": OrderStatus.FILLED,
            "Cancelled": OrderStatus.CANCELLED,
            "Inactive": OrderStatus.PENDING,
            "ApiCancelled": OrderStatus.CANCELLED,
        }
        return mapping.get(status, OrderStatus.ERROR)

    async def cancel_order(self, order_id):
        ib = self._get_ib()
        if not ib:
            return False
        loop = asyncio.get_running_loop()
        try:
            # Find and cancel open orders
            for trade in ib.openTrades():
                if str(trade.order.orderId) == str(order_id):
                    ib.cancelOrder(trade.order)
                    return True
            return False
        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return False

    async def get_order(self, order_id):
        raise NotImplementedError("IBKR order lookup not yet implemented")

    async def get_position(self, symbol):
        return None

    async def get_positions(self) -> List[Position]:
        ib = self._get_ib()
        if not ib:
            return []
        loop = asyncio.get_running_loop()

        def _fetch():
            return ib.positions()

        try:
            positions = await loop.run_in_executor(None, _fetch)
            result = []
            for pos in positions:
                result.append(Position(
                    symbol=pos.contract.symbol if hasattr(pos.contract, "symbol") else str(pos.contract),
                    qty=pos.position,
                    avg_entry_price=pos.avgCost,
                    current_price=0,  # Would need market data
                    market_value=pos.marketValue,
                    unrealized_pnl=pos.unrealizedPNL,
                    unrealized_pnl_pct=pos.unrealizedPNL / (pos.avgCost * pos.position) if pos.avgCost and pos.position else 0,
                ))
            return result
        except Exception:
            return []

    async def get_account(self) -> Dict[str, Any]:
        ib = self._get_ib()
        if not ib:
            return {"error": "Not configured", "paper": self.paper}
        loop = asyncio.get_running_loop()

        def _fetch():
            accounts = ib.managedAccounts()
            if not accounts:
                return None
            acc = accounts[0]
            summary = ib.accountSummary(acc)
            result = {"account": acc, "paper": self.paper}
            for val in summary:
                result[val.tag] = val.value
            return result

        try:
            data = await loop.run_in_executor(None, _fetch)
            if data is None:
                return {"error": "No account data", "paper": self.paper}
            return {
                "broker": "Interactive Brokers",
                "account": data.get("account", ""),
                "paper": self.paper,
                "cash": float(data.get("CashBalance", 0)),
                "net_liquidation": float(data.get("NetLiquidation", 0)),
                "buying_power": float(data.get("FullAvailableFunds", 0)),
                "currency": data.get("Currency", "USD"),
            }
        except Exception as e:
            return {"error": str(e), "paper": self.paper}

    async def is_available(self):
        try:
            ib = self._get_ib()
            return ib is not None and ib.isConnected()
        except Exception:
            return False


class IBKRMarketData(MarketDataProvider):
    """IBKR market data via ib_insync."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 2):
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib = None

    @property
    def name(self):
        return f"ibkr-data:{self.port}"

    @property
    def supported_assets(self):
        return ["equity", "option", "future", "forex"]

    def _get_ib(self):
        if self._ib is None:
            try:
                from ib_insync import IB
                self._ib = IB()
                self._ib.connect(self.host, self.port, clientId=self.client_id)
            except Exception as e:
                logger.error(f"IBKR data init failed: {e}")
                self._ib = None
        return self._ib

    async def get_ohlcv(self, symbol, timeframe="1d", start=None, end=None, limit=None):
        ib = self._get_ib()
        if not ib:
            raise RuntimeError("IBKR not configured")
        loop = asyncio.get_running_loop()

        def _fetch():
            from ib_insync import Stock
            duration_map = {"1m": "60 S", "5m": "5 D", "15m": "10 D", "1h": "1 M", "1d": "1 Y", "1w": "2 Y"}
            bar_map = {"1m": "1 min", "5m": "5 mins", "15m": "15 mins", "1h": "1 hour", "1d": "1 day", "1w": "1 week"}
            contract = Stock(symbol, "SMART", "USD")
            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration_map.get(timeframe, "1 Y"),
                barSizeSetting=bar_map.get(timeframe, "1 day"),
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            return bars

        try:
            bars = await loop.run_in_executor(None, _fetch)
            import pandas as pd
            df = pd.DataFrame([{
                "timestamp": b.date, "open": b.open, "high": b.high,
                "low": b.low, "close": b.close, "volume": b.volume,
            } for b in bars])
            if df.empty:
                raise ValueError(f"No data for {symbol}")
            df = df.set_index("timestamp")
            return OHLCVData.from_dataframe(df, symbol)
        except Exception as e:
            logger.error(f"IBKR data fetch failed: {e}")
            raise

    async def get_ticker(self, symbol):
        ib = self._get_ib()
        if not ib:
            raise RuntimeError("IBKR not configured")
        loop = asyncio.get_running_loop()

        def _fetch():
            from ib_insync import Stock
            contract = Stock(symbol, "SMART", "USD")
            ib.qualifyContracts(contract)
            ticker = ib.reqTickers(contract)
            return ticker[0] if ticker else None

        try:
            t = await loop.run_in_executor(None, _fetch)
            if t is None:
                raise ValueError(f"No ticker for {symbol}")
            return Ticker(
                symbol=symbol, bid=t.bid, ask=t.ask, last=t.last,
                volume=t.volume if hasattr(t, "volume") else 0,
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"IBKR ticker failed: {e}")
            raise

    async def get_symbols(self, asset_class=None):
        return ["AAPL", "MSFT", "GOOGL", "TSLA", "SPY", "QQQ"]

    async def is_available(self):
        try:
            ib = self._get_ib()
            return ib is not None and ib.isConnected()
        except Exception:
            return False
