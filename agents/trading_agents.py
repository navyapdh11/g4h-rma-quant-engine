"""
Specialized Trading Agents V6.0
================================
Enhanced with:
  - Proper error handling
  - Configuration validation
  - Improved signal aggregation
  - Better logging
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import numpy as np

from .base import BaseAgent, AgentRole, AgentSignal, SignalStrength
from config import AgentConfig

logger = logging.getLogger(__name__)


class ScoutAgent(BaseAgent):
    """Market Scout — Scans for trading opportunities."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("scout-01", config)
        cfg = AgentConfig()
        self.scan_interval = self.config.get("scan_interval", 5.0)
        self.z_threshold = self.config.get("z_threshold", cfg.scout_z_threshold)
        self.strong_threshold = self.config.get("strong_threshold", cfg.scout_strong_threshold)
        self.pairs_to_scan = self.config.get(
            "pairs",
            ["SPY/QQQ", "GLD/SLV", "XOM/CVX", "JPM/BAC", "KO/PEP"]
        )

    @property
    def role(self) -> AgentRole:
        return AgentRole.SCOUT

    @property
    def description(self) -> str:
        return "Scans markets for trading opportunities using statistical signals"

    async def analyze(self, market_data: Dict[str, Any]) -> AgentSignal:
        """Analyze market and return signals for ALL pairs (not just best)."""
        try:
            requested_pair = market_data.get("pair", None)
            
            all_opportunities = []
            
            for pair in self.pairs_to_scan:
                signal, confidence, reasoning = self._analyze_pair(market_data, pair)
                all_opportunities.append({
                    "pair": pair,
                    "signal": signal,
                    "confidence": confidence,
                    "reasoning": reasoning,
                })
                
                # If this is the requested pair, return it immediately
                if pair == requested_pair:
                    return AgentSignal(
                        agent_id=self.agent_id,
                        agent_role=self.role,
                        timestamp=datetime.now(timezone.utc),
                        pair=pair,
                        signal=signal,
                        confidence=confidence,
                        reasoning=reasoning,
                        metadata={
                            "pairs_scanned": len(self.pairs_to_scan),
                            "z_threshold": self.z_threshold,
                            "all_opportunities": all_opportunities,
                        }
                    )

            # If requested pair not in scan list, return best opportunity
            best = max(all_opportunities, key=lambda x: x["confidence"]) if all_opportunities else None
            
            return AgentSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                timestamp=datetime.now(timezone.utc),
                pair=best["pair"] if best else "SPY/QQQ",
                signal=best["signal"] if best else SignalStrength.HOLD,
                confidence=best["confidence"] if best else 0.0,
                reasoning=best["reasoning"] if best else "No strong opportunities detected",
                metadata={
                    "pairs_scanned": len(self.pairs_to_scan),
                    "z_threshold": self.z_threshold,
                    "all_opportunities": all_opportunities,
                }
            )
        except Exception as e:
            logger.error(f"Scout analysis error: {e}")
            return self._error_signal()

    def _analyze_pair(self, market_data: Dict[str, Any], pair: str) -> tuple:
        """Analyze a single pair."""
        kalman_z = market_data.get("kalman", {}).get(pair, {}).get("z_score", 0)

        if abs(kalman_z) > self.strong_threshold:
            signal = SignalStrength.STRONG_BUY if kalman_z < 0 else SignalStrength.STRONG_SELL
            confidence = min(abs(kalman_z) / 3.0, 1.0)
            reasoning = f"Scout: Extreme z-score {kalman_z:.2f} on {pair}"
        elif abs(kalman_z) > self.z_threshold:
            signal = SignalStrength.BUY if kalman_z < 0 else SignalStrength.SELL
            confidence = min(abs(kalman_z) / 2.5, 0.8)
            reasoning = f"Scout: Z-score {kalman_z:.2f} on {pair}"
        else:
            signal = SignalStrength.HOLD
            confidence = 0.3
            reasoning = f"Scout: Z-score {kalman_z:.2f} below threshold"

        return signal, confidence, reasoning

    def _error_signal(self) -> AgentSignal:
        return AgentSignal(
            agent_id=self.agent_id,
            agent_role=self.role,
            timestamp=datetime.now(timezone.utc),
            pair="ERROR",
            signal=SignalStrength.HOLD,
            confidence=0.0,
            reasoning="Scout: Analysis error",
            metadata={"error": True},
        )


class AnalystAgent(BaseAgent):
    """Market Analyst — Deep ML-based analysis."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("analyst-01", config)
        cfg = AgentConfig()
        self.min_ev_threshold = self.config.get("min_ev_threshold", 0.05)
        self.vol_regime_weights = {
            "LOW": 1.2, "NORMAL": 1.0, "ELEVATED": 0.7, "CRISIS": 0.3,
        }
        self.ev_weight = self.config.get("ev_weight", cfg.analyst_ev_weight)
        self.z_weight = self.config.get("z_weight", cfg.analyst_z_weight)

    @property
    def role(self) -> AgentRole:
        return AgentRole.ANALYST

    @property
    def description(self) -> str:
        return "Deep analysis using EGARCH volatility and MCTS expected value"

    async def analyze(self, market_data: Dict[str, Any]) -> AgentSignal:
        """Perform deep analysis on market data."""
        try:
            pair = market_data.get("pair", "SPY/QQQ")

            mcts_ev = market_data.get("mcts", {}).get("expected_value", 0)
            egarch_regime = market_data.get("egarch", {}).get("regime", "NORMAL")
            egarch_vol = market_data.get("egarch", {}).get("annualized_vol", 0.2)
            kalman_z = market_data.get("kalman", {}).get("z_score", 0)
            kalman_beta = market_data.get("kalman", {}).get("beta", 1.0)

            ev_score = np.tanh(mcts_ev * 10)
            z_score = np.tanh(kalman_z / 3.0)
            vol_weight = self.vol_regime_weights.get(egarch_regime, 1.0)

            composite = (ev_score * self.ev_weight + z_score * self.z_weight) * vol_weight

            if composite > 0.7:
                signal = SignalStrength.STRONG_BUY
            elif composite > 0.3:
                signal = SignalStrength.BUY
            elif composite < -0.7:
                signal = SignalStrength.STRONG_SELL
            elif composite < -0.3:
                signal = SignalStrength.SELL
            else:
                signal = SignalStrength.HOLD

            base_confidence = abs(composite)
            regime_penalty = 1.0 - (1.0 - vol_weight) * 0.5
            confidence = min(base_confidence * regime_penalty, 1.0)

            reasoning = (
                f"Analyst: EV={mcts_ev:.3f}, Z={kalman_z:.2f}, "
                f"Vol={egarch_vol:.1%} [{egarch_regime}], "
                f"Composite={composite:.3f}"
            )

            return AgentSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                timestamp=datetime.now(timezone.utc),
                pair=pair,
                signal=signal,
                confidence=confidence,
                reasoning=reasoning,
                metadata={
                    "mcts_ev": mcts_ev,
                    "kalman_z": kalman_z,
                    "kalman_beta": kalman_beta,
                    "egarch_regime": egarch_regime,
                    "egarch_vol": egarch_vol,
                    "composite_score": composite,
                }
            )
        except Exception as e:
            logger.error(f"Analyst error: {e}")
            return self._error_signal()

    def _error_signal(self) -> AgentSignal:
        return AgentSignal(
            agent_id=self.agent_id,
            agent_role=self.role,
            timestamp=datetime.now(timezone.utc),
            pair="ERROR",
            signal=SignalStrength.HOLD,
            confidence=0.0,
            reasoning="Analyst: Analysis error",
            metadata={"error": True},
        )


class TraderAgent(BaseAgent):
    """Execution Trader — Optimizes trade execution."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("trader-01", config)
        cfg = AgentConfig()
        self.default_qty = self.config.get("default_qty", 10)
        self.max_position = self.config.get("max_position", 25000)
        self.vol_adjustment = self.config.get("vol_adjustment", cfg.trader_vol_adjustment)
        self.confidence_decay = self.config.get("confidence_decay", cfg.trader_confidence_decay)

    @property
    def role(self) -> AgentRole:
        return AgentRole.TRADER

    @property
    def description(self) -> str:
        return "Optimizes trade execution timing and position sizing"

    async def analyze(self, market_data: Dict[str, Any]) -> AgentSignal:
        """Determine optimal execution strategy using raw market data."""
        try:
            pair = market_data.get("pair", "SPY/QQQ")
            egarch_vol = market_data.get("egarch", {}).get("annualized_vol", 0.2)
            kalman_z = market_data.get("kalman", {}).get("z_score", 0)
            mcts_ev = market_data.get("mcts", {}).get("expected_value", 0)

            # Compute our own signal from raw data (not from other agents' consensus)
            z_signal = SignalStrength.BUY if kalman_z < -1.5 else (SignalStrength.SELL if kalman_z > 1.5 else SignalStrength.HOLD)
            ev_signal = SignalStrength.BUY if mcts_ev > 0.05 else (SignalStrength.SELL if mcts_ev < -0.05 else SignalStrength.HOLD)
            
            # Combine signals
            if z_signal == ev_signal and z_signal != SignalStrength.HOLD:
                current_signal = z_signal
                base_confidence = min((abs(kalman_z) / 3.0 + abs(mcts_ev) * 10) / 2, 1.0)
            elif z_signal != SignalStrength.HOLD:
                current_signal = z_signal
                base_confidence = abs(kalman_z) / 3.0 * 0.7
            elif ev_signal != SignalStrength.HOLD:
                current_signal = ev_signal
                base_confidence = abs(mcts_ev) * 10 * 0.7
            else:
                current_signal = SignalStrength.HOLD
                base_confidence = 0.3

            # Volatility adjustment for execution timing
            if self.vol_adjustment:
                vol_factor = 0.2 / max(egarch_vol, 0.1)
                vol_factor = min(max(vol_factor, 0.5), 1.5)
            else:
                vol_factor = 1.0

            # Determine execution timing based on signal strength and volatility
            if current_signal == SignalStrength.STRONG_BUY:
                execution_signal = SignalStrength.BUY
                timing = "IMMEDIATE"
            elif current_signal == SignalStrength.BUY:
                execution_signal = SignalStrength.BUY
                timing = "LIMIT" if vol_factor > 1.2 else "IMMEDIATE"
            elif current_signal in [SignalStrength.STRONG_SELL, SignalStrength.SELL]:
                execution_signal = current_signal
                timing = "IMMEDIATE" if current_signal == SignalStrength.STRONG_SELL or vol_factor < 0.8 else "LIMIT"
            else:
                execution_signal = SignalStrength.HOLD
                timing = "WAIT"

            base_price = market_data.get("price_base", 450)
            notional = self.default_qty * base_price * vol_factor
            recommended_qty = int(notional / base_price)

            reasoning = (
                f"Trader: {execution_signal.value} {recommended_qty} units, "
                f"Timing: {timing}, Vol factor: {vol_factor:.2f}, "
                f"Z={kalman_z:.2f}, EV={mcts_ev:.3f}"
            )

            return AgentSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                timestamp=datetime.now(timezone.utc),
                pair=pair,
                signal=execution_signal,
                confidence=base_confidence * self.confidence_decay,
                reasoning=reasoning,
                metadata={
                    "recommended_qty": recommended_qty,
                    "timing": timing,
                    "vol_factor": vol_factor,
                    "estimated_notional": recommended_qty * base_price,
                    "derived_from_z": kalman_z,
                    "derived_from_ev": mcts_ev,
                }
            )
        except Exception as e:
            logger.error(f"Trader error: {e}")
            return self._error_signal()

    def _error_signal(self) -> AgentSignal:
        return AgentSignal(
            agent_id=self.agent_id,
            agent_role=self.role,
            timestamp=datetime.now(timezone.utc),
            pair="ERROR",
            signal=SignalStrength.HOLD,
            confidence=0.0,
            reasoning="Trader: Analysis error",
            metadata={"error": True},
        )


class RiskAgent(BaseAgent):
    """Risk Manager — Enforces risk limits."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("risk-01", config)
        self.max_daily_trades = self.config.get("max_daily_trades", 10)
        self.max_position_notional = self.config.get("max_position", 25000)
        self.max_portfolio_exposure = self.config.get("max_exposure", 100000)
        self.crisis_halt = self.config.get("crisis_halt", True)
        self.trades_today = 0
        self._last_reset_date = datetime.now(timezone.utc).date()  # Auto-reset tracking

    @property
    def role(self) -> AgentRole:
        return AgentRole.RISK

    @property
    def description(self) -> str:
        return "Enforces risk limits and monitors portfolio exposure"

    async def analyze(self, market_data: Dict[str, Any]) -> AgentSignal:
        """Evaluate risk and approve/reject trades."""
        try:
            # Auto-reset daily counter when date changes
            current_date = datetime.now(timezone.utc).date()
            if current_date != self._last_reset_date:
                logger.info(f"RiskAgent: Auto-resetting daily counter (new day: {current_date})")
                self.trades_today = 0
                self._last_reset_date = current_date

            pair = market_data.get("pair", "SPY/QQQ")
            proposed_signal = market_data.get("signal", SignalStrength.HOLD)
            proposed_qty = market_data.get("quantity", 10)
            egarch_regime = market_data.get("egarch", {}).get("regime", "NORMAL")

            if self.crisis_halt and egarch_regime == "CRISIS":
                return AgentSignal(
                    agent_id=self.agent_id,
                    agent_role=self.role,
                    timestamp=datetime.now(timezone.utc),
                    pair=pair,
                    signal=SignalStrength.HOLD,
                    confidence=1.0,
                    reasoning="Risk: CRISIS regime — all trading halted",
                    metadata={"risk_flag": "CRISIS_HALT"}
                )

            if self.trades_today >= self.max_daily_trades:
                return AgentSignal(
                    agent_id=self.agent_id,
                    agent_role=self.role,
                    timestamp=datetime.now(timezone.utc),
                    pair=pair,
                    signal=SignalStrength.HOLD,
                    confidence=1.0,
                    reasoning=f"Risk: Daily limit reached ({self.trades_today}/{self.max_daily_trades})",
                    metadata={"risk_flag": "DAILY_LIMIT"}
                )

            base_price = market_data.get("price_base", 450)
            notional = proposed_qty * base_price

            if notional > self.max_position_notional:
                return AgentSignal(
                    agent_id=self.agent_id,
                    agent_role=self.role,
                    timestamp=datetime.now(timezone.utc),
                    pair=pair,
                    signal=SignalStrength.HOLD,
                    confidence=1.0,
                    reasoning=f"Risk: Position ${notional:,.0f} exceeds limit",
                    metadata={"risk_flag": "POSITION_LIMIT"}
                )

            return AgentSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                timestamp=datetime.now(timezone.utc),
                pair=pair,
                signal=proposed_signal,
                confidence=0.95,
                reasoning=f"Risk: Approved {proposed_signal.value} {proposed_qty} units",
                metadata={
                    "approved": True,
                    "trades_today": self.trades_today,
                }
            )
        except Exception as e:
            logger.error(f"Risk error: {e}")
            return self._error_signal()

    def _error_signal(self) -> AgentSignal:
        return AgentSignal(
            agent_id=self.agent_id,
            agent_role=self.role,
            timestamp=datetime.now(timezone.utc),
            pair="ERROR",
            signal=SignalStrength.HOLD,
            confidence=0.0,
            reasoning="Risk: Analysis error",
            metadata={"error": True},
        )

    def record_trade(self, pnl: float, success: bool):
        super().record_trade(pnl, success)
        self.trades_today += 1

    def reset_daily_counter(self):
        self.trades_today = 0


class SentinelAgent(BaseAgent):
    """Market Sentinel — Black swan detection."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("sentinel-01", config)
        cfg = AgentConfig()
        self.flash_crash_threshold = self.config.get("flash_threshold", cfg.sentinel_flash_crash_threshold)
        self.vol_spike_threshold = self.config.get("vol_spike", cfg.sentinel_vol_spike_threshold)
        self.liquidity_threshold = self.config.get("liquidity", 0.5)
        self._emergency_state = False  # Track if emergency is active
        self._affected_pairs = []  # Which pairs are affected

    @property
    def role(self) -> AgentRole:
        return AgentRole.SENTINEL

    @property
    def description(self) -> str:
        return "Detects black swan events and market anomalies"

    async def analyze(self, market_data: Dict[str, Any]) -> AgentSignal:
        """Monitor for extreme events. Emits signals for each specific pair."""
        try:
            alerts = []
            pair = market_data.get("pair", "SPY/QQQ")

            returns = market_data.get("recent_returns", [])
            if returns:
                min_return = min(returns[-10:])
                if min_return < self.flash_crash_threshold:
                    alerts.append(f"Flash crash: {min_return:.1%}")

            current_vol = market_data.get("egarch", {}).get("annualized_vol", 0.2)
            avg_vol = market_data.get("avg_vol", 0.2)
            if current_vol > avg_vol * self.vol_spike_threshold:
                alerts.append(f"Vol spike: {current_vol:.1%} vs {avg_vol:.1%}")

            current_volume = market_data.get("volume", 1.0)
            avg_volume = market_data.get("avg_volume", 1.0)
            if current_volume < avg_volume * (1 - self.liquidity_threshold):
                alerts.append(f"Liquidity crisis: {current_volume:.0f} vs {avg_volume:.0f}")

            if alerts:
                self._emergency_state = True
                if pair not in self._affected_pairs:
                    self._affected_pairs.append(pair)
                return AgentSignal(
                    agent_id=self.agent_id,
                    agent_role=self.role,
                    timestamp=datetime.now(timezone.utc),
                    pair=pair,
                    signal=SignalStrength.STRONG_SELL,
                    confidence=1.0,
                    reasoning="Sentinel: " + "; ".join(alerts),
                    metadata={"alerts": alerts, "emergency": True}
                )

            self._emergency_state = False
            return AgentSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                timestamp=datetime.now(timezone.utc),
                pair=pair,
                signal=SignalStrength.HOLD,
                confidence=0.9,
                reasoning="Sentinel: No anomalies detected",
                metadata={"status": "NORMAL"}
            )
        except Exception as e:
            logger.error(f"Sentinel error: {e}")
            return self._error_signal()

    def is_emergency(self) -> bool:
        """Check if emergency state is active."""
        return self._emergency_state

    def get_affected_pairs(self) -> list:
        """Get list of affected pairs."""
        return self._affected_pairs.copy()

    def clear_emergency(self):
        """Clear emergency state."""
        self._emergency_state = False
        self._affected_pairs = []

    def _error_signal(self) -> AgentSignal:
        return AgentSignal(
            agent_id=self.agent_id,
            agent_role=self.role,
            timestamp=datetime.now(timezone.utc),
            pair="ERROR",
            signal=SignalStrength.HOLD,
            confidence=0.0,
            reasoning="Sentinel: Analysis error",
            metadata={"error": True},
        )


def create_agent(role: str, config: Optional[Dict[str, Any]] = None) -> BaseAgent:
    """Create an agent by role name."""
    agents = {
        "scout": ScoutAgent,
        "analyst": AnalystAgent,
        "trader": TraderAgent,
        "risk": RiskAgent,
        "sentinel": SentinelAgent,
    }

    if role not in agents:
        raise ValueError(f"Unknown agent role: {role}")

    return agents[role](config)
