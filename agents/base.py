"""
Multi-Agent Trading System — Base Agent Architecture
=====================================================
Autonomous agents that collaborate to make trading decisions.

Agent Types:
  - Scout: Scans markets for opportunities
  - Analyst: Deep analysis using ML models
  - Trader: Executes trades with optimal timing
  - Risk: Monitors and enforces risk limits
  - Sentinel: Watches for black swan events

Each agent:
  - Runs asynchronously
  - Has specialized expertise
  - Communicates via shared state
  - Makes independent recommendations
  - Votes on final decisions
"""
from __future__ import annotations
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    SCOUT = "scout"
    ANALYST = "analyst"
    TRADER = "trader"
    RISK = "risk"
    SENTINEL = "sentinel"
    ORCHESTRATOR = "orchestrator"


class SignalStrength(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


@dataclass
class AgentSignal:
    """Signal from an agent."""
    agent_id: str
    agent_role: AgentRole
    timestamp: datetime
    pair: str
    signal: SignalStrength
    confidence: float  # 0.0 to 1.0
    reasoning: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def numeric_value(self) -> float:
        """Convert signal to numeric value for aggregation."""
        mapping = {
            SignalStrength.STRONG_BUY: 1.0,
            SignalStrength.BUY: 0.5,
            SignalStrength.HOLD: 0.0,
            SignalStrength.SELL: -0.5,
            SignalStrength.STRONG_SELL: -1.0,
        }
        return mapping[self.signal] * self.confidence
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_role": self.agent_role.value,
            "timestamp": self.timestamp.isoformat(),
            "pair": self.pair,
            "signal": self.signal.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "numeric_value": self.numeric_value,
            **self.metadata
        }


@dataclass
class AgentState:
    """Current state of an agent."""
    agent_id: str
    agent_role: AgentRole
    status: str  # active, idle, error
    last_update: datetime
    decisions_made: int
    successful_trades: int
    failed_trades: int
    total_pnl: float
    current_analysis: Optional[Dict[str, Any]] = None


class BaseAgent(ABC):
    """Abstract base class for all trading agents."""
    
    def __init__(self, agent_id: str, config: Optional[Dict[str, Any]] = None):
        self.agent_id = agent_id
        self.config = config or {}
        self.state = AgentState(
            agent_id=agent_id,
            agent_role=self.role,
            status="idle",
            last_update=datetime.now(timezone.utc),
            decisions_made=0,
            successful_trades=0,
            failed_trades=0,
            total_pnl=0.0,
        )
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    @property
    @abstractmethod
    def role(self) -> AgentRole:
        """Agent's role."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""
        pass
    
    @abstractmethod
    async def analyze(self, market_data: Dict[str, Any]) -> AgentSignal:
        """
        Analyze market data and produce a signal.
        
        Args:
            market_data: Current market data including:
                - prices: Dict of symbol -> price
                - ohlcv: OHLCV data for each symbol
                - indicators: Technical indicators
                - signals: Signals from other agents
        
        Returns:
            AgentSignal with recommendation
        """
        pass
    
    async def start(self):
        """Start the agent's background tasks."""
        self._running = True
        self.state.status = "active"
        logger.info(f"Agent {self.agent_id} ({self.role.value}) started")
    
    async def stop(self):
        """Stop the agent's background tasks."""
        self._running = False
        self.state.status = "idle"
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"Agent {self.agent_id} ({self.role.value}) stopped")
    
    async def run_loop(self, interval: float = 5.0):
        """Run agent's main loop."""
        await self.start()
        try:
            while self._running:
                await self._tick()
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
    
    async def _tick(self):
        """Single iteration of agent's work."""
        try:
            # To be implemented by subclasses for background tasks
            pass
        except Exception as e:
            logger.error(f"Agent {self.agent_id} tick error: {e}")
            self.state.status = "error"
    
    def record_trade(self, pnl: float, success: bool):
        """Record a trade result."""
        self.state.decisions_made += 1
        self.state.total_pnl += pnl
        if success:
            self.state.successful_trades += 1
        else:
            self.state.failed_trades += 1
        self.state.last_update = datetime.now(timezone.utc)
    
    def get_state(self) -> Dict[str, Any]:
        """Get current agent state as dict."""
        return {
            "agent_id": self.state.agent_id,
            "role": self.state.agent_role.value,
            "status": self.state.status,
            "last_update": self.state.last_update.isoformat(),
            "decisions_made": self.state.decisions_made,
            "successful_trades": self.state.successful_trades,
            "failed_trades": self.state.failed_trades,
            "total_pnl": self.state.total_pnl,
            "win_rate": (
                self.state.successful_trades / self.state.decisions_made
                if self.state.decisions_made > 0 else 0.0
            ),
            "description": self.description,
        }


class AgentOrchestrator:
    """
    Coordinates multiple agents and aggregates their signals.
    Implements consensus-based decision making.
    """
    
    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self.signals: Dict[str, List[AgentSignal]] = {}  # pair -> signals
        self._running = False
        self._consensus_threshold = 0.6  # 60% agreement needed
        self._min_agents = 2  # Minimum agents for consensus
    
    def register_agent(self, agent: BaseAgent):
        """Register an agent with the orchestrator."""
        self.agents[agent.agent_id] = agent
        logger.info(f"Registered agent: {agent.agent_id} ({agent.role.value})")
    
    def unregister_agent(self, agent_id: str):
        """Unregister an agent."""
        if agent_id in self.agents:
            del self.agents[agent_id]
    
    async def start_all(self):
        """Start all registered agents."""
        tasks = [agent.start() for agent in self.agents.values()]
        await asyncio.gather(*tasks)
        self._running = True
    
    async def stop_all(self):
        """Stop all registered agents."""
        self._running = False
        tasks = [agent.stop() for agent in self.agents.values()]
        await asyncio.gather(*tasks)
    
    def submit_signal(self, signal: AgentSignal):
        """Submit a signal from an agent."""
        if signal.pair not in self.signals:
            self.signals[signal.pair] = []
        
        # Keep last 10 signals per pair
        self.signals[signal.pair].append(signal)
        self.signals[signal.pair] = self.signals[signal.pair][-10:]
    
    def get_consensus(self, pair: str) -> Tuple[SignalStrength, float, str]:
        """
        Get consensus signal for a pair.
        
        Returns:
            (signal, confidence, reasoning)
        """
        if pair not in self.signals or not self.signals[pair]:
            return SignalStrength.HOLD, 0.0, "No signals available"
        
        signals = self.signals[pair][-5:]  # Last 5 signals
        
        # Calculate weighted average
        values = [s.numeric_value for s in signals]
        weights = [s.confidence for s in signals]
        
        if not values:
            return SignalStrength.HOLD, 0.0, "No valid signals"
        
        avg_value = np.average(values, weights=weights)
        avg_confidence = np.mean(weights)
        
        # Determine signal strength
        if avg_value > 0.7:
            signal = SignalStrength.STRONG_BUY
        elif avg_value > 0.3:
            signal = SignalStrength.BUY
        elif avg_value < -0.7:
            signal = SignalStrength.STRONG_SELL
        elif avg_value < -0.3:
            signal = SignalStrength.SELL
        else:
            signal = SignalStrength.HOLD
        
        # Build reasoning
        reasoning = f"Consensus from {len(signals)} signals: "
        reasoning += f"avg={avg_value:.3f}, conf={avg_confidence:.1%}"
        
        return signal, avg_confidence, reasoning
    
    def get_all_states(self) -> List[Dict[str, Any]]:
        """Get state of all agents."""
        return [agent.get_state() for agent in self.agents.values()]
    
    def get_active_agents(self) -> List[str]:
        """Get list of active agent IDs."""
        return [
            agent_id for agent_id, agent in self.agents.items()
            if agent.state.status == "active"
        ]
    
    async def run_background_monitor(self, interval: float = 10.0):
        """Run background monitoring of all agents."""
        while self._running:
            for agent in self.agents.values():
                if agent.state.status == "error":
                    logger.warning(f"Agent {agent.agent_id} in error state")
                    # Attempt restart
                    await agent.stop()
                    await agent.start()
            await asyncio.sleep(interval)
