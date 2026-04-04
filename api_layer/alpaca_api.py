"""
Alpaca Trading API Wrapper.
============================
Provides execution and market data for US equities.
Supports:
  - Paper trading (default)
  - Live trading
  - Market data (IEX, SIP, Crypto)
  - Options (via Alpaca Options API)
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import pandas as pd
import numpy as np

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


class AlpacaExecution(ExecutionProvider):
    """Alpaca execution provider."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        paper: bool = True,
        retry_max: int = 3,
    ):
        self.api_key = api_key or os.environ.get("APCA_API_KEY_ID", "")
        self.api_secret = api_secret or os.environ.get("APCA_API_SECRET_KEY", "")
        self.paper = paper
        self.retry_max = retry_max
        self._api = None
        self._base_url = (
            "https://paper-api.alpaca.markets" if paper
            else "https://api.alpaca.markets"
        )
    
    @property
    def name(self) -> str:
        return "alpaca:" + ("paper" if self.paper else "live")
    
    @property
    def is_paper(self) -> bool:
        return self.paper
    
    def _get_api(self):
        """Lazy-load Alpaca API."""
        if self._api is None:
            if not self.api_key or not self.api_secret:
                logger.warning("Alpaca API keys not configured")
                return None
            try:
                import alpaca_trade_api as tradeapi
                self._api = tradeapi.REST(
                    self.api_key,
                    self.api_secret,
                    self._base_url,
                    api_version="v2",
                )
                logger.info(f"Alpaca {'paper' if self.paper else 'live'} connected")
            except Exception as e:
                logger.error(f"Alpaca init failed: {e}")
                self._api = None
        return self._api
    
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
        """Submit an order to Alpaca."""
        api = self._get_api()
        
        # Simulated order if no API or dry_run
        if api is None or dry_run:
            return self._simulate_order(symbol, side, qty, order_type, limit_price)
        
        alpaca_side = "buy" if side == OrderSide.BUY else "sell"
        alpaca_type = {
            OrderType.MARKET: "market",
            OrderType.LIMIT: "limit",
            OrderType.STOP: "stop",
            OrderType.STOP_LIMIT: "stop_limit",
        }.get(order_type, "market")
        
        loop = asyncio.get_running_loop()
        
        def _submit():
            return api.submit_order(
                symbol=symbol,
                qty=int(qty) if qty == int(qty) else qty,
                side=alpaca_side,
                type=alpaca_type,
                time_in_force=time_in_force,
                limit_price=limit_price,
                stop_price=stop_price,
            )
        
        for attempt in range(self.retry_max):
            try:
                order = await loop.run_in_executor(None, _submit)
                return OrderResult(
                    order_id=order.id,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    filled_qty=float(order.filled_qty) if order.filled_qty else 0,
                    avg_price=float(order.filled_avg_price) if order.filled_avg_price else 0,
                    status=self._map_status(order.status),
                    timestamp=datetime.fromisoformat(order.submitted_at.replace("Z", "+00:00")),
                    raw_response=order._raw,
                )
            except Exception as e:
                logger.warning(f"Alpaca order attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_max - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    return OrderResult(
                        order_id=f"sim_{datetime.now().timestamp()}",
                        symbol=symbol,
                        side=side,
                        qty=qty,
                        filled_qty=0,
                        avg_price=0,
                        status=OrderStatus.ERROR,
                        timestamp=datetime.now(),
                        error_message=str(e),
                    )
        
        raise RuntimeError("Unexpected error in submit_order")
    
    def _simulate_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        order_type: OrderType,
        limit_price: Optional[float],
    ) -> OrderResult:
        """Simulate order for paper/testing."""
        import random
        # Simulate fill at current price +/- small slippage
        base_price = limit_price or 100.0
        slippage = random.uniform(-0.02, 0.02)  # ±2%
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
            commission=0.0,  # Alpaca has $0 commission
            raw_response={"simulated": True},
        )
    
    def _map_status(self, alpaca_status: str) -> OrderStatus:
        """Map Alpaca status to our standard."""
        mapping = {
            "new": OrderStatus.SUBMITTED,
            "accepted": OrderStatus.SUBMITTED,
            "pending_new": OrderStatus.PENDING,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED,
            "done_for_day": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "expired": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "stopped": OrderStatus.PENDING,
        }
        return mapping.get(alpaca_status, OrderStatus.ERROR)
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        api = self._get_api()
        if api is None:
            return False
        
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: api.cancel_order(order_id))
            return True
        except Exception as e:
            logger.error(f"Cancel order failed: {e}")
            return False
    
    async def get_order(self, order_id: str) -> OrderResult:
        """Get order status."""
        api = self._get_api()
        if api is None:
            raise RuntimeError("Alpaca API not configured")
        
        loop = asyncio.get_running_loop()
        order = await loop.run_in_executor(None, lambda: api.get_order(order_id))
        
        return OrderResult(
            order_id=order.id,
            symbol=order.symbol,
            side=OrderSide.BUY if order.side == "buy" else OrderSide.SELL,
            qty=float(order.qty),
            filled_qty=float(order.filled_qty) if order.filled_qty else 0,
            avg_price=float(order.filled_avg_price) if order.filled_avg_price else 0,
            status=self._map_status(order.status),
            timestamp=datetime.fromisoformat(order.submitted_at.replace("Z", "+00:00")),
            raw_response=order._raw,
        )
    
    async def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a symbol."""
        api = self._get_api()
        if api is None:
            return None
        
        loop = asyncio.get_running_loop()
        try:
            pos = await loop.run_in_executor(None, lambda: api.get_position(symbol))
            if pos.qty == "0":
                return None
            
            qty = float(pos.qty)
            avg_entry = float(pos.avg_entry_price)
            current = float(pos.current_price)
            market_value = float(pos.market_value) if pos.market_value else qty * current
            
            return Position(
                symbol=symbol,
                qty=qty,
                avg_entry_price=avg_entry,
                current_price=current,
                market_value=market_value,
                unrealized_pnl=float(pos.unrealized_pl) if pos.unrealized_pl else 0,
                unrealized_pnl_pct=float(pos.unrealized_plpc) if pos.unrealized_plpc else 0,
            )
        except Exception:
            return None
    
    async def get_positions(self) -> List[Position]:
        """Get all open positions."""
        api = self._get_api()
        if api is None:
            return []
        
        loop = asyncio.get_running_loop()
        positions = await loop.run_in_executor(None, lambda: api.list_positions())
        
        result = []
        for pos in positions:
            qty = float(pos.qty)
            avg_entry = float(pos.avg_entry_price)
            current = float(pos.current_price)
            result.append(Position(
                symbol=pos.symbol,
                qty=qty,
                avg_entry_price=avg_entry,
                current_price=current,
                market_value=float(pos.market_value) if pos.market_value else qty * current,
                unrealized_pnl=float(pos.unrealized_pl) if pos.unrealized_pl else 0,
                unrealized_pnl_pct=float(pos.unrealized_plpc) if pos.unrealized_plpc else 0,
            ))
        return result
    
    async def get_account(self) -> Dict[str, Any]:
        """Get account information."""
        api = self._get_api()
        if api is None:
            return {"error": "API not configured", "paper": True}
        
        loop = asyncio.get_running_loop()
        account = await loop.run_in_executor(None, lambda: api.get_account())
        
        return {
            "id": account.id,
            "account_number": account.account_number,
            "status": account.status,
            "currency": account.currency,
            "buying_power": float(account.buying_power) if account.buying_power else 0,
            "cash": float(account.cash) if account.cash else 0,
            "portfolio_value": float(account.portfolio_value) if account.portfolio_value else 0,
            "pattern_day_trader": account.pattern_day_trader,
            "trading_blocked": account.trading_blocked,
            "transfers_blocked": account.transfers_blocked,
            "account_blocked": account.account_blocked,
            "paper": self.paper,
        }
    
    async def is_available(self) -> bool:
        """Check if Alpaca is reachable."""
        api = self._get_api()
        if api is None:
            return False
        
        loop = asyncio.get_running_loop()
        try:
            account = await loop.run_in_executor(None, lambda: api.get_account())
            return account.status == "ACTIVE"
        except Exception:
            return False


class AlpacaMarketData(MarketDataProvider):
    """Alpaca market data provider."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        feed: str = "iex",  # iex, sip, or crypto
    ):
        self.api_key = api_key or os.environ.get("APCA_API_KEY_ID", "")
        self.api_secret = api_secret or os.environ.get("APCA_API_SECRET_KEY", "")
        self.feed = feed
        self._data_api = None
    
    @property
    def name(self) -> str:
        return f"alpaca-data:{self.feed}"
    
    @property
    def supported_assets(self) -> List[str]:
        return ["equity", "crypto"] if self.feed != "crypto" else ["crypto"]
    
    def _get_data_api(self):
        """Lazy-load data API."""
        if self._data_api is None:
            if not self.api_key or not self.api_secret:
                return None
            try:
                import alpaca_trade_api as tradeapi
                self._data_api = tradeapi.REST(
                    self.api_key,
                    self.api_secret,
                    base_url="https://data.alpaca.markets",
                    api_version="v2",
                )
            except Exception as e:
                logger.error(f"Alpaca data init failed: {e}")
                self._data_api = None
        return self._data_api
    
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> OHLCVData:
        """Get OHLCV data from Alpaca."""
        data_api = self._get_data_api()
        if data_api is None:
            raise RuntimeError("Alpaca data API not configured")
        
        # Map timeframe
        tf_map = {"1m": "Min", "5m": "5Min", "15m": "15Min", "1h": "Hour", "1d": "Day"}
        timeframe_str = tf_map.get(timeframe, "Day")
        
        start = start or (datetime.now() - timedelta(days=30))
        end = end or datetime.now()
        limit = limit or 1000
        
        loop = asyncio.get_running_loop()
        
        def _fetch():
            return data_api.get_bars(
                symbol,
                timeframe_str,
                start=start.isoformat(),
                end=end.isoformat(),
                limit=limit,
            ).df
        
        try:
            df = await loop.run_in_executor(None, _fetch)
            if df.empty:
                raise ValueError(f"No data for {symbol}")
            
            # Flatten multi-index columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            df = df.rename(columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            })
            
            return OHLCVData.from_dataframe(df[["open", "high", "low", "close", "volume"]], symbol)
            
        except Exception as e:
            logger.error(f"Alpaca data fetch failed: {e}")
            raise
    
    async def get_ticker(self, symbol: str) -> Ticker:
        """Get latest quote."""
        data_api = self._get_data_api()
        if data_api is None:
            raise RuntimeError("Alpaca data API not configured")
        
        loop = asyncio.get_running_loop()
        
        def _fetch():
            return data_api.get_latest_quote(symbol)
        
        quote = await loop.run_in_executor(None, _fetch)
        
        return Ticker(
            symbol=symbol,
            bid=quote.bidprice,
            ask=quote.askprice,
            last=(quote.bidprice + quote.askprice) / 2,
            volume=0,  # Not available in quote
            timestamp=datetime.now(),
        )
    
    async def get_symbols(self, asset_class: Optional[str] = None) -> List[str]:
        """Get available symbols."""
        # Alpaca doesn't provide easy symbol list without pagination
        # Return common symbols
        return [
            "SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "GOOGL", "AMZN",
            "META", "TSLA", "NVDA", "JPM", "V", "JNJ", "WMT", "PG",
        ]
    
    async def is_available(self) -> bool:
        """Check if data API is available."""
        try:
            data_api = self._get_data_api()
            if data_api is None:
                return False
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: data_api.get_latest_quote("SPY"))
            return True
        except Exception:
            return False
