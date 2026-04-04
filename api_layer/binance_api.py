"""
Binance Execution Provider.
============================
Full execution + market data via CCXT Binance.
Supports:
  - Spot trading
  - Futures (USDT-M)
  - Paper trading (testnet)
  - Market data (OHLCV, tickers, order book)
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


class BinanceExecution(ExecutionProvider):
    """Binance execution provider via CCXT."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = True,
        default_type: str = "spot",  # spot or future
    ):
        self.api_key = api_key or os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("BINANCE_API_SECRET", "")
        self.testnet = testnet
        self.default_type = default_type
        self._exchange = None

    @property
    def name(self) -> str:
        return "binance:" + ("testnet" if self.testnet else "live")

    @property
    def is_paper(self) -> bool:
        return self.testnet

    def _get_exchange(self):
        """Lazy-load CCXT Binance."""
        if self._exchange is None:
            import ccxt
            try:
                self._exchange = getattr(ccxt, "binance")({
                    "apiKey": self.api_key,
                    "secret": self.api_secret,
                    "enableRateLimit": True,
                    "timeout": 30000,
                    "options": {
                        "defaultType": self.default_type,
                        "testnet": self.testnet,
                    },
                })
                logger.info(f"Binance {'testnet' if self.testnet else 'live'} connected")
            except Exception as e:
                logger.error(f"Binance init failed: {e}")
                self._exchange = None
        return self._exchange

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
        exchange = self._get_exchange()
        if exchange is None or dry_run:
            return self._simulate_order(symbol, side, qty, order_type, limit_price)

        ccxt_side = "buy" if side == OrderSide.BUY else "sell"
        ccxt_type = {
            OrderType.MARKET: "market",
            OrderType.LIMIT: "limit",
            OrderType.STOP: "stop_loss",
            OrderType.STOP_LIMIT: "stop",
        }.get(order_type, "market")

        params: Dict[str, Any] = {}
        if stop_price:
            params["stopPrice"] = stop_price

        loop = asyncio.get_running_loop()

        def _submit():
            return exchange.create_order(
                symbol=symbol,
                type=ccxt_type,
                side=ccxt_side,
                amount=qty,
                price=limit_price,
                params=params,
            )

        try:
            order = await loop.run_in_executor(None, _submit)
            return OrderResult(
                order_id=str(order.get("id", f"binance_{datetime.now().timestamp()}")),
                symbol=order.get("symbol", symbol),
                side=side,
                qty=qty,
                filled_qty=order.get("filled", 0),
                avg_price=order.get("average", 0) or order.get("price", 0),
                status=self._map_status(order.get("status", "open")),
                timestamp=datetime.fromtimestamp(order.get("timestamp", 0) / 1000) if order.get("timestamp") else datetime.now(),
                commission=order.get("fee", {}).get("cost", 0),
                raw_response=order,
            )
        except Exception as e:
            logger.error(f"Binance order failed: {e}")
            return OrderResult(
                order_id=f"binance_err_{datetime.now().timestamp()}",
                symbol=symbol,
                side=side,
                qty=qty,
                filled_qty=0,
                avg_price=0,
                status=OrderStatus.ERROR,
                timestamp=datetime.now(),
                error_message=str(e),
            )

    def _simulate_order(self, symbol, side, qty, order_type, limit_price):
        import random
        base_price = limit_price or 100.0
        slippage = random.uniform(-0.005, 0.005)
        fill_price = base_price * (1 + slippage)
        return OrderResult(
            order_id=f"sim_{datetime.now().timestamp()}",
            symbol=symbol,
            side=side,
            qty=qty,
            filled_qty=qty,
            avg_price=fill_price,
            status=OrderStatus.FILLED,
            timestamp=datetime.now(),
            commission=0,
            raw_response={"simulated": True, "provider": "binance"},
        )

    def _map_status(self, status: str) -> OrderStatus:
        mapping = {
            "open": OrderStatus.SUBMITTED,
            "new": OrderStatus.SUBMITTED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED,
            "closed": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "expired": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }
        return mapping.get(status, OrderStatus.ERROR)

    async def cancel_order(self, order_id: str) -> bool:
        exchange = self._get_exchange()
        if exchange is None:
            return False
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: exchange.cancel_order(order_id))
            return True
        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return False

    async def get_order(self, order_id: str) -> OrderResult:
        exchange = self._get_exchange()
        if exchange is None:
            raise RuntimeError("Binance not configured")
        loop = asyncio.get_running_loop()
        order = await loop.run_in_executor(None, lambda: exchange.fetch_order(order_id))
        return OrderResult(
            order_id=str(order["id"]),
            symbol=order["symbol"],
            side=OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL,
            qty=order["amount"],
            filled_qty=order.get("filled", 0),
            avg_price=order.get("average", 0) or order.get("price", 0),
            status=self._map_status(order.get("status", "open")),
            timestamp=datetime.fromtimestamp(order["timestamp"] / 1000),
            raw_response=order,
        )

    async def get_position(self, symbol: str) -> Optional[Position]:
        return None  # Binance spot has no position tracking via CCXT easily

    async def get_positions(self) -> List[Position]:
        exchange = self._get_exchange()
        if exchange is None:
            return []
        loop = asyncio.get_running_loop()
        try:
            balance = await loop.run_in_executor(None, exchange.fetch_balance)
            positions = []
            for currency, total in balance.get("total", {}).items():
                if total and total > 0:
                    free = balance.get("free", {}).get(currency, 0)
                    positions.append(Position(
                        symbol=currency,
                        qty=total,
                        avg_entry_price=0,
                        current_price=0,
                        market_value=0,
                        unrealized_pnl=0,
                        unrealized_pnl_pct=0,
                    ))
            return positions
        except Exception:
            return []

    async def get_account(self) -> Dict[str, Any]:
        exchange = self._get_exchange()
        if exchange is None:
            return {"error": "Not configured", "paper": self.testnet}
        loop = asyncio.get_running_loop()
        try:
            balance = await loop.run_in_executor(None, exchange.fetch_balance)
            usdt = balance.get("free", {}).get("USDT", 0)
            return {
                "exchange": "Binance",
                "testnet": self.testnet,
                "currency": "USDT",
                "free_usdt": usdt,
                "total_balance": sum(v for k, v in balance.get("total", {}).items() if v and k in ("USDT", "BUSD", "USD")),
            }
        except Exception as e:
            return {"error": str(e), "paper": self.testnet}

    async def is_available(self) -> bool:
        try:
            exchange = self._get_exchange()
            if exchange is None:
                return False
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: exchange.fetch_ohlcv("BTC/USDT", "1d", limit=1))
            return True
        except Exception:
            return False


class BinanceMarketData(MarketDataProvider):
    """Binance market data via CCXT."""

    def __init__(self, testnet: bool = True):
        self.testnet = testnet
        self._exchange = None
        self._cache: Dict[str, tuple] = {}

    @property
    def name(self) -> str:
        return f"binance-data:{'testnet' if self.testnet else 'live'}"

    @property
    def supported_assets(self) -> List[str]:
        return ["crypto"]

    def _get_exchange(self):
        if self._exchange is None:
            import ccxt
            self._exchange = getattr(ccxt, "binance")({
                "enableRateLimit": True,
                "timeout": 30000,
                "options": {"defaultType": "spot", "testnet": self.testnet},
            })
        return self._exchange

    async def get_ohlcv(self, symbol: str, timeframe: str = "1d", start=None, end=None, limit=None) -> OHLCVData:
        exchange = self._get_exchange()
        tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}
        ccxt_tf = tf_map.get(timeframe, "1d")
        loop = asyncio.get_running_loop()
        bars = await loop.run_in_executor(None, lambda: exchange.fetch_ohlcv(symbol, timeframe=ccxt_tf, limit=limit or 500))
        import pandas as pd
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp").sort_index()
        return OHLCVData.from_dataframe(df, symbol)

    async def get_ticker(self, symbol: str) -> Ticker:
        exchange = self._get_exchange()
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, lambda: exchange.fetch_ticker(symbol))
        return Ticker(
            symbol=symbol,
            bid=data.get("bid", 0),
            ask=data.get("ask", 0),
            last=data.get("last", 0),
            volume=data.get("baseVolume", 0),
            timestamp=datetime.now(),
        )

    async def get_symbols(self, asset_class=None) -> List[str]:
        exchange = self._get_exchange()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, exchange.load_markets)
        return [s for s in exchange.symbols if "USDT" in s]

    async def is_available(self) -> bool:
        try:
            exchange = self._get_exchange()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: exchange.fetch_ohlcv("BTC/USDT", "1d", limit=1))
            return True
        except Exception:
            return False
