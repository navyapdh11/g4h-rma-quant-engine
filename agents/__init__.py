"""
Multi-Agent Trading System
===========================
Autonomous agents collaborating for optimal trading decisions.

Quick Start:
    from agents import get_trading_engine

    engine = get_trading_engine()
    engine.start(auto_trade=True)
"""
from .base import (
    BaseAgent,
    AgentRole,
    AgentSignal,
    AgentState,
    SignalStrength,
    AgentOrchestrator,
)
from .trading_agents import (
    ScoutAgent,
    AnalystAgent,
    TraderAgent,
    RiskAgent,
    SentinelAgent,
    create_agent,
)
from .engine import (
    RealTimeTradingEngine,
    get_trading_engine,
)
from .got_reasoning import (
    GoTReasoner,
    GoTGraph,
    ThoughtNode,
    ThoughtType,
    get_got_reasoner,
)

__all__ = [
    # Base
    "BaseAgent",
    "AgentRole",
    "AgentSignal",
    "AgentState",
    "SignalStrength",
    "AgentOrchestrator",
    # Trading Agents
    "ScoutAgent",
    "AnalystAgent",
    "TraderAgent",
    "RiskAgent",
    "SentinelAgent",
    "create_agent",
    # Engine
    "RealTimeTradingEngine",
    "get_trading_engine",
    # GoT Reasoning
    "GoTReasoner",
    "GoTGraph",
    "ThoughtNode",
    "ThoughtType",
    "get_got_reasoner",
]
