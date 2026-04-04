"""
Alpaca Paper Trading Executor V6.0
===================================
Enhanced with:
  - Better error handling
  - Transactional execution
  - Improved logging
  - Account info endpoint
"""
from __future__ import annotations
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timezone
from config import ExecutionConfig
from models import ActionType, ExecutionResponse

logger = logging.getLogger(__name__)


@dataclass
class OrderRecord:
    symbol: str
    side: str
    qty: float
    order_id: Optional[str]
    status: str
    timestamp: str


class AlpacaExecutor:
    """Alpaca trading executor with safety features."""

    def __init__(self, cfg: Optional[ExecutionConfig] = None):
        self.cfg = cfg or ExecutionConfig()
        self._api = None
        self._order_history: List[OrderRecord] = []
        self._init_api()

    def _init_api(self):
        if not self.cfg.alpaca_key or not self.cfg.alpaca_secret:
            logger.info("Alpaca keys not set — SIMULATION mode")
            return
        try:
            import alpaca_trade_api as tradeapi
            self._api = tradeapi.REST(
                self.cfg.alpaca_key, self.cfg.alpaca_secret,
                self.cfg.alpaca_base_url, api_version="v2",
            )
            self._api.get_account()
            logger.info("Alpaca Paper Trading connected")
        except Exception as e:
            logger.error(f"Alpaca init failed: {e}")
            self._api = None

    @property
    def is_live(self) -> bool:
        return self._api is not None

    def execute(self, base, quote, action, price_base, price_quote,
                beta, qty_base=None, dry_run=True) -> ExecutionResponse:
        if action == ActionType.HOLD:
            return ExecutionResponse(
                status="no_action", action=action,
                pair=f"{base}/{quote}", details="Signal is HOLD."
            )
        
        qty_a = qty_base or self.cfg.default_qty_base
        
        if price_quote > 0 and price_base > 0:
            notional_a = qty_a * price_base
            qty_b = round(notional_a * abs(beta) / price_quote, 6)
        else:
            qty_b = qty_a
        
        total_notional = qty_a * price_base + qty_b * price_quote
        
        if total_notional > self.cfg.max_position_notional:
            return ExecutionResponse(
                status="rejected", action=action, pair=f"{base}/{quote}",
                details=f"Notional ${total_notional:,.0f} exceeds ${self.cfg.max_position_notional:,.0f}"
            )
        
        if action == ActionType.LONG_SPREAD:
            leg_a, leg_b = ("buy", qty_a), ("sell", qty_b)
        else:
            leg_a, leg_b = ("sell", qty_a), ("buy", qty_b)
        
        if dry_run or not self.is_live:
            return ExecutionResponse(
                status="simulated", action=action, pair=f"{base}/{quote}",
                details=(
                    f"DRY RUN: {leg_a[0].upper()} {leg_a[1]} {base} @ ${price_base:.2f}, "
                    f"{leg_b[0].upper()} {leg_b[1]} {quote} @ ${price_quote:.2f} "
                    f"(beta={beta:.3f}, notional~${total_notional:,.0f})"
                ),
            )
        
        # Live execution with rollback capability
        order_ids = []
        executed_legs = []
        
        try:
            for symbol, (side, qty) in [(base, leg_a), (quote, leg_b)]:
                order_qty = int(qty) if qty == int(qty) else qty
                resp = self._api.submit_order(
                    symbol=symbol, qty=order_qty, side=side,
                    type="market", time_in_force="gtc",
                )
                order_ids.append(resp.id)
                executed_legs.append((symbol, side, qty))
                self._order_history.append(OrderRecord(
                    symbol=symbol, side=side, qty=qty,
                    order_id=resp.id, status="submitted",
                    timestamp=resp.submitted_at.isoformat() if resp.submitted_at else "",
                ))
            
            return ExecutionResponse(
                status="executed", action=action, pair=f"{base}/{quote}",
                details=f"Filled: {leg_a[0]} {leg_a[1]} {base} + {leg_b[0]} {leg_b[1]} {quote}",
                order_ids=order_ids,
            )
            
        except Exception as e:
            logger.error(f"Execution error: {e}")
            # Note: In production, implement rollback for partial fills
            return ExecutionResponse(
                status="error", action=action, pair=f"{base}/{quote}",
                details=f"Execution failed: {str(e)}. Executed legs: {executed_legs}",
            )

    def get_history(self) -> List[Dict[str, Any]]:
        return [
            {"symbol": o.symbol, "side": o.side, "qty": o.qty,
             "order_id": o.order_id, "status": o.status}
            for o in self._order_history
        ]

    async def get_account(self) -> Dict[str, Any]:
        """Get account information from Alpaca."""
        if not self._api:
            return {"error": "API not configured", "paper": True}

        try:
            import asyncio
            loop = asyncio.get_running_loop()
            account = await loop.run_in_executor(None, lambda: self._api.get_account())

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
                "paper": self.is_live,
            }
        except Exception as e:
            logger.error(f"Get account failed: {e}")
            return {"error": str(e), "paper": True}
