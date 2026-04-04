"""
Risk Manager — Portfolio-level safety net V6.0
===============================================
V6.0 Features:
  - Position-level tracking with PnL monitoring
  - Stop-loss and take-profit enforcement
  - Correlation monitoring
  - VaR calculation
  - Circuit breakers for extreme conditions
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from models import ActionType, VolatilityRegime, TradeSignal
from config import RiskConfig
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class PositionRecord:
    pair: str
    action: ActionType
    entry_z: float
    entry_spread: float
    entry_time: datetime
    quantity: float
    stop_loss_z: float
    take_profit_z: float
    current_pnl: float = 0.0
    max_pnl: float = 0.0
    min_pnl: float = 0.0


class RiskManager:
    """Comprehensive risk management system."""

    def __init__(self, cfg: Optional[RiskConfig] = None):
        self.cfg = cfg or RiskConfig()
        self._daily_trades: Dict[str, int] = {}
        self._active_positions: Dict[str, PositionRecord] = {}
        self._daily_pnl: Dict[str, float] = {}
        self._pnl_history: List[float] = []
        self._correlation_matrix: Dict[str, Dict[str, float]] = {}
        self._circuit_breaker_open: bool = False
        self._max_drawdown: float = 0.0
        self._peak_pnl: float = 0.0

    def approve(self, signal: TradeSignal) -> Tuple[bool, str]:
        if self._circuit_breaker_open:
            return False, "CIRCUIT_BREAKER_OPEN"
        
        if self.cfg.crisis_halt and signal.egarch.regime == VolatilityRegime.CRISIS:
            return False, "CRISIS_REGIME_HALT"
        
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = self._daily_trades.get(today, 0)
        if count >= self.cfg.max_daily_trades:
            return False, f"DAILY_LIMIT ({count}/{self.cfg.max_daily_trades})"
        
        if signal.pair in self._active_positions:
            if signal.action == self._active_positions[signal.pair].action:
                return False, f"DUPLICATE_POSITION ({signal.pair})"
        
        if signal.confidence < 0.3:  # Default confidence threshold
            return False, f"LOW_CONFIDENCE ({signal.confidence:.2f})"
        
        if self._max_drawdown >= self.cfg.max_drawdown_halt:
            return False, f"MAX_DRAWDOWN ({self._max_drawdown:.1%})"
        
        return True, "APPROVED"

    def record_trade(self, pair: str, action: ActionType, 
                     entry_z: float = 0.0, entry_spread: float = 0.0,
                     quantity: float = 1.0):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._daily_trades[today] = self._daily_trades.get(today, 0) + 1
        
        if action == ActionType.HOLD:
            if pair in self._active_positions:
                pos = self._active_positions[pair]
                self._record_pnl(pos.current_pnl)
                del self._active_positions[pair]
        else:
            self._active_positions[pair] = PositionRecord(
                pair=pair, action=action, entry_z=entry_z,
                entry_spread=entry_spread,
                entry_time=datetime.now(timezone.utc),
                quantity=quantity,
                stop_loss_z=self.cfg.stop_loss_z,
                take_profit_z=self.cfg.take_profit_z,
            )

    def _record_pnl(self, pnl: float):
        self._pnl_history.append(pnl)
        if len(self._pnl_history) > 252:
            self._pnl_history.pop(0)
        
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._daily_pnl[today] = self._daily_pnl.get(today, 0) + pnl
        
        cumulative = sum(self._pnl_history)
        if cumulative > self._peak_pnl:
            self._peak_pnl = cumulative
        drawdown = (self._peak_pnl - cumulative) / max(self._peak_pnl, 1)
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown
        
        if self._daily_pnl.get(today, 0) <= -self.cfg.max_daily_loss:
            self._circuit_breaker_open = True
            logger.warning("Circuit breaker triggered: daily loss limit")

    def update_position_pnl(self, pair: str, current_z: float, 
                           current_spread: float, current_pnl: float):
        if pair not in self._active_positions:
            return None
        
        pos = self._active_positions[pair]
        pos.current_pnl = current_pnl
        
        if current_pnl > pos.max_pnl:
            pos.max_pnl = current_pnl
        if current_pnl < pos.min_pnl:
            pos.min_pnl = current_pnl
        
        if self.cfg.stop_loss_enabled:
            if current_z >= pos.stop_loss_z or current_z <= -pos.stop_loss_z:
                logger.warning(f"Stop-loss triggered for {pair}: z={current_z:.2f}")
                return "STOP_LOSS"
            
            if abs(current_z) <= pos.take_profit_z:
                logger.info(f"Take-profit triggered for {pair}: z={current_z:.2f}")
                return "TAKE_PROFIT"
        
        return None

    def get_var_95(self) -> Optional[float]:
        if len(self._pnl_history) < 30:
            return None
        return float(np.percentile(self._pnl_history, 5))

    def get_expected_shortfall(self) -> Optional[float]:
        if len(self._pnl_history) < 30:
            return None
        var_95 = self.get_var_95()
        if var_95 is None:
            return None
        tail_losses = [p for p in self._pnl_history if p <= var_95]
        if not tail_losses:
            return None
        return float(np.mean(tail_losses))

    def update_correlation(self, pair: str, other_pair: str, correlation: float):
        if pair not in self._correlation_matrix:
            self._correlation_matrix[pair] = {}
        self._correlation_matrix[pair][other_pair] = correlation

    def check_correlation_risk(self, pair: str) -> Tuple[bool, str]:
        if pair not in self._correlation_matrix:
            return True, "OK"
        
        for existing_pair in self._active_positions.keys():
            corr = self._correlation_matrix.get(pair, {}).get(existing_pair, 0)
            if abs(corr) > self.cfg.max_correlation:
                return False, f"HIGH_CORRELATION ({corr:.2f})"
        
        return True, "OK"

    def reset_daily_counters(self):
        self._daily_trades.clear()
        self._daily_pnl.clear()
        self._circuit_breaker_open = False

    def get_risk_metrics(self) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {
            "total_exposure": len(self._active_positions),
            "daily_pnl": self._daily_pnl.get(today, 0),
            "daily_trades": self._daily_trades.get(today, 0),
            "max_daily_trades": self.cfg.max_daily_trades,
            "active_positions": len(self._active_positions),
            "crisis_mode": self._circuit_breaker_open,
            "drawdown": self._max_drawdown,
            "var_95": self.get_var_95(),
            "expected_shortfall": self.get_expected_shortfall(),
            "max_drawdown": self._max_drawdown,
            "peak_pnl": self._peak_pnl,
        }

    def get_active_positions(self) -> Dict[str, dict]:
        return {
            pair: {
                "action": pos.action.value,
                "entry_z": pos.entry_z,
                "entry_spread": pos.entry_spread,
                "current_pnl": pos.current_pnl,
                "max_pnl": pos.max_pnl,
                "min_pnl": pos.min_pnl,
                "entry_time": pos.entry_time.isoformat(),
            }
            for pair, pos in self._active_positions.items()
        }
