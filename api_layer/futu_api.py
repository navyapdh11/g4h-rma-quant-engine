"""
Futu (FutuOpenAPI) Execution Provider.
=======================================
Execution + market data for Futu / Moomoo / NiuNiu.
Supports:
  - Hong Kong stocks
  - US stocks
  - China A-shares (Stock Connect)
  - HK/US options
  - Paper trading (Futu simulation)
Requires: futu-api Python package
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


class FutuExecution(ExecutionProvider):
    """Futu execution provider via futu-api (OpenD gateway)."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        paper_trading: bool = True,
        market: str = "US",  # US, HK, CN
    ):
        self.host = host or os.environ.get("FUTU_OPEND_HOST", "127.0.0.1")
        self.port = int(os.environ.get("FUTU_OPEND_PORT", port))
        self.paper = paper_trading
        self.market = market
        self._open_quote = None
        self._open_trade = None

    @property
    def name(self) -> str:
        return "futu:" + ("paper" if self.paper else "live")

    @property
    def is_paper(self) -> bool:
        return self.paper

    def _get_trade_ctx(self):
        """Lazy-load Futu OpenD trade context."""
        if self._open_trade is None:
            try:
                from futu import OpenQuoteContext, OpenSecTradeContext, TrdMarket
                self._open_quote = OpenQuoteContext(host=self.host, port=self.port)

                market_map = {"US": TrdMarket.US, "HK": TrdMarket.HK, "CN": TrdMarket.CN}
                trd_env = None  # real environment
                if self.paper:
                    from futu import TrdEnv
                    trd_env = TrdEnv.SIMULATE

                self._open_trade = OpenSecTradeContext(
                    filter_trdmarket=market_map.get(self.market, TrdMarket.US),
                    host=self.host,
                    port=self.port,
                    trd_env=trd_env,
                )
                logger.info(f"Futu {'paper' if self.paper else 'live'} {self.market} connected")
            except ImportError:
                logger.error("futu-api package not installed. pip install futu-api")
                self._open_trade = None
            except Exception as e:
                logger.error(f"Futu OpenD connection failed: {e}")
                self._open_trade = None
        return self._open_trade

    def _get_quote_ctx(self):
        if self._open_quote is None:
            self._get_trade_ctx()  # Initialize both
        return self._open_quote

    def _to_futu_code(self, symbol: str) -> str:
        """Convert symbol to Futu format (e.g., US.AAPL, HK.00700)."""
        if "." in symbol:
            return symbol
        prefix = {"US": "US.", "HK": "HK.", "CN": "SH."}.get(self.market, "US.")
        return f"{prefix}{symbol}"

    async def submit_order(self, symbol, side, qty, order_type=OrderType.MARKET,
                           limit_price=None, stop_price=None, time_in_force="gtc",
                           dry_run=False) -> OrderResult:
        if dry_run:
            return self._simulate_order(symbol, side, qty, order_type, limit_price)

        trd_ctx = self._get_trade_ctx()
        if trd_ctx is None:
            return self._simulate_order(symbol, side, qty, order_type, limit_price)

        loop = asyncio.get_running_loop()

        def _submit():
            from futu import TrdSide, OrderType as FutuOT, ModifyOrderOp, TrdEnv
            futu_side = TrdSide.BUY if side == OrderSide.BUY else TrdSide.SELL
            futu_type = FutuOT.NORMAL if order_type == OrderType.MARKET else FutuOT.ABSOLUTE

            price = limit_price or 0
            code = self._to_futu_code(symbol)

            ret, data = trd_ctx.place_order(
                price=price,
                qty=qty,
                code=code,
                trd_side=futu_side,
                order_type=futu_type,
            )
            if ret == 0:
                return {"success": True, "order_id": data.iloc[0].get("order_id", "")}
            else:
                return {"success": False, "error": str(data)}

        try:
            result = await loop.run_in_executor(None, _submit)
            if result.get("success"):
                return OrderResult(
                    order_id=str(result["order_id"]),
                    symbol=symbol, side=side, qty=qty,
                    filled_qty=qty, avg_price=limit_price or 0,
                    status=OrderStatus.SUBMITTED, timestamp=datetime.now(),
                    raw_response=result,
                )
            else:
                return OrderResult(
                    order_id=f"futu_err_{datetime.now().timestamp()}",
                    symbol=symbol, side=side, qty=qty,
                    filled_qty=0, avg_price=0,
                    status=OrderStatus.ERROR, timestamp=datetime.now(),
                    error_message=result.get("error", "Unknown"),
                )
        except Exception as e:
            logger.error(f"Futu order failed: {e}")
            return OrderResult(
                order_id=f"futu_exc_{datetime.now().timestamp()}",
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
            raw_response={"simulated": True, "provider": "futu"},
        )

    async def cancel_order(self, order_id):
        trd_ctx = self._get_trade_ctx()
        if not trd_ctx:
            return False
        loop = asyncio.get_running_loop()
        try:
            from futu import ModifyOrderOp
            ret, _ = trd_ctx.modify_order(ModifyOrderOp.CANCEL, order_id, 0, 0)
            return ret == 0
        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return False

    async def get_order(self, order_id):
        raise NotImplementedError("Futu order lookup not yet implemented")

    async def get_position(self, symbol):
        return None

    async def get_positions(self) -> List[Position]:
        trd_ctx = self._get_trade_ctx()
        if not trd_ctx:
            return []
        loop = asyncio.get_running_loop()

        def _fetch():
            ret, data = trd_ctx.position_list_query()
            if ret == 0:
                return data
            return None

        try:
            data = await loop.run_in_executor(None, _fetch)
            if data is None or data.empty:
                return []
            result = []
            for _, row in data.iterrows():
                result.append(Position(
                    symbol=row.get("code", ""),
                    qty=float(row.get("qty", 0)),
                    avg_entry_price=float(row.get("cost_price", 0)),
                    current_price=float(row.get("nominal_price", 0)),
                    market_value=float(row.get("market_val", 0)),
                    unrealized_pnl=float(row.get("pl_val", 0)),
                    unrealized_pnl_pct=float(row.get("pl_ratio", 0)),
                ))
            return result
        except Exception:
            return []

    async def get_account(self) -> Dict[str, Any]:
        trd_ctx = self._get_trade_ctx()
        if not trd_ctx:
            return {"error": "Not configured", "paper": self.paper}
        loop = asyncio.get_running_loop()

        def _fetch():
            ret, data = trd_ctx.accinfo_query()
            if ret == 0:
                return data
            return None

        try:
            data = await loop.run_in_executor(None, _fetch)
            if data is None or data.empty:
                return {"error": "No account data", "paper": self.paper}
            row = data.iloc[0]
            return {
                "broker": "Futu",
                "market": self.market,
                "paper": self.paper,
                "currency": row.get("currency", "USD"),
                "cash": float(row.get("cash", 0)),
                "market_value": float(row.get("market_val", 0)),
                "total_assets": float(row.get("total_assets", 0)),
                "buying_power": float(row.get("power", 0)),
            }
        except Exception as e:
            return {"error": str(e), "paper": self.paper}

    async def is_available(self):
        try:
            trd_ctx = self._get_trade_ctx()
            return trd_ctx is not None
        except Exception:
            return False


class FutuMarketData(MarketDataProvider):
    """Futu market data provider."""

    def __init__(self, host: str = "127.0.0.1", port: int = 11111, market: str = "US"):
        self.host = host
        self.port = port
        self.market = market
        self._quote_ctx = None

    @property
    def name(self):
        return f"futu-data:{self.market}"

    @property
    def supported_assets(self):
        return ["equity", "option"]

    def _get_quote_ctx(self):
        if self._quote_ctx is None:
            try:
                from futu import OpenQuoteContext
                self._quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
            except Exception as e:
                logger.error(f"Futu quote init failed: {e}")
                self._quote_ctx = None
        return self._quote_ctx

    async def get_ohlcv(self, symbol, timeframe="1d", start=None, end=None, limit=None):
        quote_ctx = self._get_quote_ctx()
        if quote_ctx is None:
            raise RuntimeError("Futu not configured")
        loop = asyncio.get_running_loop()

        def _fetch():
            from futu import KLType, SubType, AuType
            kl_map = {"1m": KLType.K_1M, "5m": KLType.K_5M, "15m": KLType.K_15M,
                      "1h": KLType.K_60M, "1d": KLType.K_DAY, "1w": KLType.K_WEEK, "1mo": KLType.K_MON}
            prefix = {"US": "US.", "HK": "HK.", "CN": "SH."}.get(self.market, "US.")
            code = symbol if "." in symbol else f"{prefix}{symbol}"
            ret, data = quote_ctx.request_history_kline(
                code, ktype=kl_map.get(timeframe, KLType.K_DAY),
                max_count=limit or 500,
            )
            if ret == 0:
                return data
            return None

        try:
            data = await loop.run_in_executor(None, _fetch)
            if data is None or data.empty:
                raise ValueError(f"No data for {symbol}")
            import pandas as pd
            df = data.rename(columns={
                "time_key": "timestamp", "open": "open", "high": "high",
                "low": "low", "close": "close", "volume": "volume",
            })
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            return OHLCVData.from_dataframe(df.set_index("timestamp"), symbol)
        except Exception as e:
            logger.error(f"Futu data fetch failed: {e}")
            raise

    async def get_ticker(self, symbol):
        quote_ctx = self._get_quote_ctx()
        if not quote_ctx:
            raise RuntimeError("Futu not configured")
        loop = asyncio.get_running_loop()

        def _fetch():
            from futu import SubType
            prefix = {"US": "US.", "HK": "HK.", "CN": "SH."}.get(self.market, "US.")
            code = symbol if "." in symbol else f"{prefix}{symbol}"
            quote_ctx.subscribe(code, [SubType.QUOTE])
            ret, data = quote_ctx.get_market_snapshot([code])
            if ret == 0 and not data.empty:
                return data.iloc[0]
            return None

        try:
            row = await loop.run_in_executor(None, _fetch)
            if row is None:
                raise ValueError(f"No ticker for {symbol}")
            return Ticker(
                symbol=symbol,
                bid=float(row.get("bid_price", 0)),
                ask=float(row.get("ask_price", 0)),
                last=float(row.get("last_price", 0)),
                volume=float(row.get("volume", 0)),
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Ticker fetch failed: {e}")
            raise

    async def get_symbols(self, asset_class=None):
        return ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "META"]

    async def is_available(self):
        try:
            return self._get_quote_ctx() is not None
        except Exception:
            return False
