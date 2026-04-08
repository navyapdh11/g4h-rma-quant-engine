"""
Real-Time Trading Engine with WebSocket Support
================================================
Provides:
  - Live market data streaming
  - Real-time agent signals
  - Auto-trading with agent consensus
  - WebSocket endpoints for dashboard
"""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, asdict

from .base import AgentOrchestrator, AgentSignal, SignalStrength, AgentRole
from .trading_agents import create_agent

# V10.1: Centralized fallback data (avoid duplication across methods)
_FALLBACK_MARKET_DATA = {
    "pair": "SPY/QQQ",
    "price_base": 450.0,
    "price_quote": 400.0,
    "data_points": 0,
    "kalman": {"z_score": 0.0, "beta": 1.0, "alpha": 0.0, "spread": 0.0, "innovation_var": 1.0, "converged": False, "is_divergent": True},
    "egarch": {"annualized_vol": 0.2, "forecast_vol": 0.2, "regime": "NORMAL", "leverage_gamma": None},
    "mcts": {"expected_value": 0.0, "action": "HOLD", "visit_distribution": {}},
}

logger = logging.getLogger(__name__)


@dataclass
class TradeDecision:
    """A trading decision from the agent collective."""
    timestamp: str
    pair: str
    signal: str
    confidence: float
    consensus_reasoning: str
    agent_signals: List[Dict[str, Any]]
    action: str  # EXECUTE, HOLD, REJECT
    executed: bool = False
    execution_price: Optional[float] = None
    pnl: Optional[float] = None


class RealTimeTradingEngine:
    """
    Real-time trading engine powered by multi-agent system.
    
    Features:
    - Continuous market monitoring
    - Agent-based decision making
    - Auto-execution (optional)
    - WebSocket broadcasting
    """
    
    def __init__(self, orchestrator: AgentOrchestrator, executor=None):
        self.orchestrator = orchestrator
        self.executor = executor  # Trade executor (from execution module)
        self._running = False
        self._auto_trade = False
        self._scan_interval = 10.0  # seconds
        self._websocket_clients: Set = set()
        self._decisions: List[TradeDecision] = []
        self._active_trades: Dict[str, TradeDecision] = {}
        self._market_data: Dict[str, Any] = {}
        self._got_state: Dict[str, Any] = {}  # GoT reasoning state (keyed by pair)
        self._got_state_lock = asyncio.Lock()  # Protect _got_state dict
    
    def start(self, auto_trade: bool = False):
        """Start the trading engine."""
        self._running = True
        self._auto_trade = auto_trade
        logger.info(f"Trading engine started (auto_trade={auto_trade})")
    
    def stop(self):
        """Stop the trading engine."""
        self._running = False
        self._auto_trade = False
        logger.info("Trading engine stopped")

    def _check_emergency_state(self) -> bool:
        """Check if Sentinel has detected an emergency."""
        sentinel = self.orchestrator.agents.get("sentinel-01")
        if sentinel and hasattr(sentinel, 'is_emergency'):
            return sentinel.is_emergency()
        return False

    def _get_affected_pairs(self) -> list:
        """Get list of pairs affected by Sentinel emergency."""
        sentinel = self.orchestrator.agents.get("sentinel-01")
        if sentinel and hasattr(sentinel, 'get_affected_pairs'):
            return sentinel.get_affected_pairs()
        return []
    
    async def run_loop(self):
        """Main trading loop."""
        self.start()
        
        try:
            while self._running:
                await self._tick()
                await asyncio.sleep(self._scan_interval)
        except asyncio.CancelledError:
            pass
        finally:
            self.stop()
    
    async def _tick(self):
        """Single iteration of trading loop with GoT reasoning."""
        try:
            # 1. Fetch fresh market data
            await self._fetch_market_data()

            # 2. Check for Sentinel emergency state before running agents
            emergency_active = self._check_emergency_state()

            # 3. Run all agents
            agent_signals = await self._run_agents()

            # 4. Get consensus + GoT reasoning for each pair
            for pair in self._market_data.keys():
                # Include Sentinel signal in consensus if emergency is active
                signal, confidence, reasoning = self.orchestrator.get_consensus(pair)

                # Emergency override: if Sentinel detected anomaly, force STRONG_SELL
                if emergency_active and pair in self._get_affected_pairs():
                    signal = SignalStrength.STRONG_SELL
                    confidence = max(confidence, 0.8)  # At least 0.8 confidence
                    reasoning += " [EMERGENCY: Sentinel override active]"

                # ── GoT Graph-of-Thought Reasoning ──────────────────────
                got_result = None
                try:
                    from .got_reasoning import get_got_reasoner
                    from core.semantic_cache import get_semantic_cache

                    reasoner = get_got_reasoner()
                    cache = get_semantic_cache()

                    # Gather market data for GoT
                    market = self._market_data[pair]
                    kalman = market.get("kalman", {})
                    egarch = market.get("egarch", {})
                    mcts = market.get("mcts", {})

                    # Build agent signal summaries for GoT
                    pair_signals = [
                        {"agent_id": s.agent_id, "signal": s.signal.value, "confidence": s.confidence}
                        for s in agent_signals if s.pair == pair
                    ]

                    # GoT tick with semantic cache for expensive reasoning
                    got_key = f"got:{pair}:{kalman.get('z_score', 0):.2f}:{egarch.get('regime', 'NORMAL')}:{mcts.get('expected_value', 0):.3f}"
                    got_result = await cache.get_or_compute(
                        got_key,
                        lambda p=pair, k=kalman, e=egarch, m=mcts, ps=pair_signals: reasoner.process_tick(
                            pair=p,
                            kalman_z=k.get("z_score", 0),
                            egarch_regime=e.get("regime", "NORMAL"),
                            egarch_vol=e.get("annualized_vol", 0.2),
                            mcts_ev=m.get("expected_value", 0),
                            mcts_action=m.get("action", "HOLD"),
                            agent_signals=ps,
                        ),
                        ttl=300,  # 5 min TTL for GoT results
                        text_for_embedding=f"GoT reasoning for {pair} with Kalman Z={kalman.get('z_score', 0):.2f} and MCTS EV={mcts.get('expected_value', 0):.3f}",
                    )

                    # Augment reasoning with GoT decision if actionable
                    if got_result and got_result.get("actionable"):
                        got_decision = got_result.get("decision", {})
                        reasoning += f" | GoT: {got_decision.get('content', '')}"
                except Exception as e:
                    logger.debug(f"GoT reasoning error for {pair}: {e}")
                # ────────────────────────────────────────────────────────

                # 5. Create decision
                decision = self._create_decision(pair, signal, confidence, reasoning, agent_signals)

                # Enhance decision with GoT data
                if got_result:
                    async with self._got_state_lock:
                        self._got_state[pair] = got_result

                # 6. Execute if auto-trading enabled
                if self._auto_trade and decision.action == "EXECUTE":
                    await self._execute_decision(decision)

                # 7. Broadcast to WebSocket clients
                await self._broadcast(decision)

        except Exception as e:
            logger.error(f"Trading engine tick error: {e}")
    
    async def _fetch_market_data(self):
        """Fetch latest market data from configured pairs.
        
        V8.0: Enriched with Kalman filter, EGARCH, and MCTS data
        so all agents receive the complete signal structure they expect.
        """
        try:
            from config import settings
            from data.fetcher import DataFetcher
            from core.kalman import MultivariateKalmanFilter
            from core.egarch import EGARCHVolatilityModel
            from core.mcts import MCTSEngine

            fetcher = DataFetcher(settings.data)
            egarch_model = EGARCHVolatilityModel(settings.egarch)
            mcts_engine = MCTSEngine(settings.mcts)
            market_data = {}

            # Scan equity pairs
            for base, quote in settings.universe.equity_pairs[:5]:
                try:
                    df = await fetcher.get_yfinance(base, quote, "5d")
                    if df is None or len(df) < 10:
                        continue

                    pair_key = f"{base}/{quote}"
                    price_base = float(df[base].iloc[-1])
                    price_quote = float(df[quote].iloc[-1])

                    # Run Kalman filter over full history
                    kf = MultivariateKalmanFilter(settings.kalman)
                    snapshot = None
                    pa, pb = df[base], df[quote]
                    for i in range(len(pa)):
                        snapshot = kf.step(float(pa.iloc[i]), float(pb.iloc[i]))

                    # EGARCH analysis
                    egarch_result = egarch_model.analyze(pa, cache_key=base)
                    vol_scale = egarch_model.get_vol_scale(egarch_result.regime)

                    # MCTS decision
                    mcts_result = mcts_engine.search(
                        snapshot.spread if snapshot else 0.0,
                        snapshot.innovation_var if snapshot else 1.0,
                        vol_scale,
                    )

                    market_data[pair_key] = {
                        "pair": pair_key,
                        "price_base": price_base,
                        "price_quote": price_quote,
                        "data_points": len(df),
                        # V8.0: Full signal structure for all agents
                        "kalman": {
                            "z_score": snapshot.pure_z_score if snapshot else 0.0,
                            "beta": snapshot.beta if snapshot else 1.0,
                            "alpha": snapshot.alpha if snapshot else 0.0,
                            "spread": snapshot.spread if snapshot else 0.0,
                            "innovation_var": snapshot.innovation_var if snapshot else 1.0,
                            "converged": snapshot.converged if snapshot else False,
                            "is_divergent": snapshot.is_divergent if snapshot else True,
                        },
                        "egarch": {
                            "annualized_vol": egarch_result.annualized_vol,
                            "forecast_vol": egarch_result.forecast_vol,
                            "regime": egarch_result.regime.value,
                            "leverage_gamma": egarch_result.leverage_gamma,
                        },
                        "mcts": {
                            "expected_value": mcts_result.expected_value,
                            "action": mcts_result.action.value,
                            "visit_distribution": mcts_result.visit_distribution,
                        },
                    }
                except Exception as e:
                    logger.debug(f"Failed to fetch {base}/{quote}: {e}")
                    continue

            # V10.1: Use centralized fallback data instead of duplicated constants
            if not market_data:
                logger.warning("All market data fetches failed, using minimal fallback")
                market_data = {
                    "SPY/QQQ": dict(_FALLBACK_MARKET_DATA),
                }

            self._market_data = market_data

        except Exception as e:
            logger.error(f"Market data fetch error: {e}")
            self._market_data = {
                "SPY/QQQ": dict(_FALLBACK_MARKET_DATA),
            }
    
    async def _run_agents(self) -> List[AgentSignal]:
        """Run all agents on current market data."""
        signals = []

        for agent in self.orchestrator.agents.values():
            try:
                for pair, data in self._market_data.items():
                    data["pair"] = pair
                    signal = await agent.analyze(data)
                    self.orchestrator.submit_signal(signal)
                    signals.append(signal)
            except Exception as e:
                logger.error(f"Agent {agent.agent_id} analysis error: {e}")

        # V10.1: Integrate DashScope AI analysis (if available)
        try:
            from .ai_agent import get_ai_agent
            ai_agent = get_ai_agent()
            
            if ai_agent.is_available():
                for pair, data in self._market_data.items():
                    kalman = data.get("kalman", {})
                    egarch = data.get("egarch", {})
                    mcts = data.get("mcts", {})
                    
                    # Get current consensus to pass to AI
                    current_signal, current_confidence, _ = self.orchestrator.get_consensus(pair)
                    
                    ai_analysis = await ai_agent.analyze_signal(
                        pair=pair,
                        signal=current_signal.value,
                        confidence=current_confidence,
                        kalman_z=kalman.get("z_score", 0),
                        egarch_regime=egarch.get("regime", "NORMAL"),
                        mcts_ev=mcts.get("expected_value", 0),
                    )
                    
                    # Convert AI analysis to AgentSignal
                    ai_confidence = 0.7 if ai_analysis.get("assessment") == "STRONG" else (0.5 if ai_analysis.get("assessment") == "MODERATE" else 0.3)
                    ai_signal_strength = ai_analysis.get("position_size", "HALF")
                    
                    if ai_signal_strength == "FULL" and ai_confidence > 0.6:
                        ai_signal = SignalStrength.BUY
                    elif ai_signal_strength == "QUARTER" or ai_analysis.get("risk_level") == "HIGH":
                        ai_signal = SignalStrength.SELL
                    else:
                        ai_signal = SignalStrength.HOLD
                    
                    ai_agent_signal = AgentSignal(
                        agent_id="ai-dashscope",
                        agent_role=AgentRole.ANALYST,
                        timestamp=datetime.now(timezone.utc),
                        pair=pair,
                        signal=ai_signal,
                        confidence=ai_confidence,
                        reasoning=f"AI: {ai_analysis.get('reasoning', 'No analysis')}",
                        metadata=ai_analysis,
                    )
                    
                    self.orchestrator.submit_signal(ai_agent_signal)
                    signals.append(ai_agent_signal)
        except Exception as e:
            logger.debug(f"DashScope AI integration error: {e}")

        return signals
    
    def _create_decision(
        self,
        pair: str,
        signal: SignalStrength,
        confidence: float,
        reasoning: str,
        agent_signals: List[AgentSignal],
    ) -> TradeDecision:
        """Create a trading decision."""
        # Determine action
        if signal in [SignalStrength.STRONG_BUY, SignalStrength.BUY]:
            action = "EXECUTE" if confidence > 0.6 else "HOLD"
            trade_action = "LONG_SPREAD"
        elif signal in [SignalStrength.STRONG_SELL, SignalStrength.SELL]:
            action = "EXECUTE" if confidence > 0.6 else "HOLD"
            trade_action = "SHORT_SPREAD"
        else:
            action = "HOLD"
            trade_action = "HOLD"
        
        # Get agent signals for this pair
        pair_signals = [s for s in agent_signals if s.pair == pair]
        
        decision = TradeDecision(
            timestamp=datetime.now(timezone.utc).isoformat(),
            pair=pair,
            signal=signal.value,
            confidence=confidence,
            consensus_reasoning=reasoning,
            agent_signals=[s.to_dict() for s in pair_signals],
            action=action,
            executed=False,
        )
        
        self._decisions.append(decision)
        self._decisions = self._decisions[-50:]  # Keep last 50
        
        return decision
    
    async def _execute_decision(self, decision: TradeDecision):
        """Execute a trading decision."""
        if not self.executor:
            logger.warning("No executor configured — simulating")
            decision.executed = True
            decision.execution_price = 0.0
            return
        
        try:
            # Parse pair
            base, quote = decision.pair.split("/")
            
            # Map signal to action
            action_map = {
                "LONG_SPREAD": "LONG_SPREAD",
                "SHORT_SPREAD": "SHORT_SPREAD",
            }
            action = action_map.get(decision.signal, "HOLD")
            
            # Execute
            result = await self.executor.execute(
                base=base,
                quote=quote,
                action=action,
                dry_run=not self._auto_trade,  # Dry run unless auto-trade enabled
            )
            
            decision.executed = result.status in ["executed", "simulated"]
            
            if decision.executed:
                self._active_trades[decision.pair] = decision
                logger.info(f"Executed: {decision.signal} {decision.pair}")
            
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            decision.action = "REJECT"
    
    async def _broadcast(self, decision: TradeDecision):
        """Broadcast decision to WebSocket clients."""
        if not self._websocket_clients:
            return
        
        message = {
            "type": "decision",
            "data": asdict(decision),
        }
        
        # Send to all connected clients
        disconnected = set()
        for client in self._websocket_clients:
            try:
                await client.send_json(message)
            except Exception:
                disconnected.add(client)
        
        # Remove disconnected clients
        self._websocket_clients -= disconnected
    
    def add_websocket_client(self, client):
        """Add a WebSocket client."""
        self._websocket_clients.add(client)
        logger.info(f"WebSocket client connected. Total: {len(self._websocket_clients)}")
    
    def remove_websocket_client(self, client):
        """Remove a WebSocket client."""
        self._websocket_clients.discard(client)
        logger.info(f"WebSocket client disconnected. Total: {len(self._websocket_clients)}")
    
    def get_decisions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent decisions."""
        return [asdict(d) for d in self._decisions[-limit:]]
    
    def get_active_trades(self) -> List[Dict[str, Any]]:
        """Get active trades."""
        return [asdict(t) for t in self._active_trades.values()]
    
    def get_engine_status(self) -> Dict[str, Any]:
        """Get engine status."""
        return {
            "running": self._running,
            "auto_trade": self._auto_trade,
            "decisions_count": len(self._decisions),
            "active_trades": len(self._active_trades),
            "websocket_clients": len(self._websocket_clients),
            "agents": self.orchestrator.get_all_states(),
            "got_state": self._got_state,
        }

    def get_got_state(self) -> Dict[str, Any]:
        """Get the current Graph-of-Thought reasoning state."""
        try:
            from .got_reasoning import get_got_reasoner
            reasoner = get_got_reasoner()
            return reasoner.get_dashboard_state()
        except Exception as e:
            logger.debug(f"GoT state export error: {e}")
            return {"error": str(e)}


# Global engine instance
_engine: Optional[RealTimeTradingEngine] = None


def get_trading_engine() -> RealTimeTradingEngine:
    """Get or create the global trading engine."""
    global _engine

    if _engine is None:
        # Create orchestrator
        orchestrator = AgentOrchestrator()

        # Create and register agents
        agent_configs = {
            "scout": {"pairs": ["SPY/QQQ", "GLD/SLV", "XOM/CVX"]},
            "analyst": {"min_ev_threshold": 0.05},
            "trader": {"default_qty": 10},
            "risk": {"max_daily_trades": 10},
            "sentinel": {},
        }

        for role, config in agent_configs.items():
            agent = create_agent(role, config)
            orchestrator.register_agent(agent)

        # V7.0: Import and pass executor for actual trade execution
        try:
            from config import settings
            from execution.alpaca import AlpacaExecutor
            executor = AlpacaExecutor(settings.execution)
        except Exception as e:
            logger.warning(f"Executor init failed: {e}. Running in simulation mode.")
            executor = None

        # Create engine with executor
        _engine = RealTimeTradingEngine(orchestrator, executor=executor)

    return _engine
