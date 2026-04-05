"""
Enterprise data models — strict typing, validation, documentation.
V8.0: Unified version, BUY/SELL actions, enhanced models.
"""
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator
import re


class ActionType(str, Enum):
    LONG_SPREAD = "LONG_SPREAD"
    SHORT_SPREAD = "SHORT_SPREAD"
    HOLD = "HOLD"
    BUY = "BUY"
    SELL = "SELL"


class VolatilityRegime(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    CRISIS = "CRISIS"


class SignalSource(str, Enum):
    KALMAN_MCTS = "KALMAN_MCTS"
    COINTEGRATION = "COINTEGRATION"
    BACKTEST = "BACKTEST"
    AGENT_CONSENSUS = "AGENT_CONSENSUS"  # V6.0


class AgentRole(str, Enum):
    SCOUT = "scout"
    ANALYST = "analyst"
    TRADER = "trader"
    RISK = "risk"
    SENTINEL = "sentinel"


class AssetPair(BaseModel):
    """Validated asset pair."""
    base: str = Field(..., min_length=1, max_length=10, pattern=r"^[A-Z0-9\-\./]+$")
    quote: str = Field(..., min_length=1, max_length=10, pattern=r"^[A-Z0-9\-\./]+$")
    
    @model_validator(mode='after')
    def validate_not_same(self):
        if self.base == self.quote:
            raise ValueError("Base and quote cannot be the same")
        return self


class ScanRequest(BaseModel):
    """Validated scan request."""
    pairs: List[AssetPair] = Field(..., min_length=1, max_length=20)
    mcts_iterations: int = Field(800, ge=100, le=5000)
    source: SignalSource = SignalSource.KALMAN_MCTS


class BacktestRequest(BaseModel):
    """Validated backtest request."""
    base: str = Field(..., min_length=1, max_length=10)
    quote: str = Field(..., min_length=1, max_length=10)
    start: str = Field("2020-01-01", pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    initial_capital: float = Field(100000.0, gt=0, le=10000000)
    z_entry: float = Field(2.0, gt=0, le=5.0)
    z_exit: float = Field(0.0, ge=-1.0, le=1.0)
    mcts_enabled: bool = True
    stop_loss_z: Optional[float] = Field(None, gt=3.0, le=10.0)  # V6.0


class ExecuteRequest(BaseModel):
    """Validated execution request."""
    base: str = Field(..., min_length=1, max_length=10, pattern=r"^[A-Z0-9\-\./]+$")
    quote: str = Field(..., min_length=1, max_length=10, pattern=r"^[A-Z0-9\-\./]+$")
    action: ActionType
    qty: Optional[int] = Field(None, ge=1, le=10000)
    dry_run: bool = True
    
    @field_validator('base', 'quote')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        if not re.match(r"^[A-Z0-9\-\./]+$", v):
            raise ValueError(f"Invalid symbol format: {v}")
        return v


class KalmanState(BaseModel):
    """Kalman filter state snapshot."""
    beta: float
    alpha: float
    spread: float
    innovation_variance: float
    pure_z_score: float
    converged: bool = True  # V6.0


class EGARCHResult(BaseModel):
    """EGARCH volatility analysis result."""
    annualized_vol: float = Field(ge=0, le=3.0)  # Increased max to 300%
    forecast_vol: float = Field(ge=0, le=3.0)
    leverage_gamma: Optional[float] = Field(None, ge=-1.0, le=1.0)
    regime: VolatilityRegime
    params: Optional[Dict[str, Any]] = None  # Changed to Any for flexibility


class MCTSResult(BaseModel):
    """MCTS decision result."""
    action: ActionType
    expected_value: float
    visit_distribution: Dict[str, int] = Field(default_factory=dict)
    avg_reward_distribution: Dict[str, float] = Field(default_factory=dict)


class TradeSignal(BaseModel):
    """Complete trading signal with all components."""
    pair: str
    action: ActionType
    confidence: float = Field(ge=0.0, le=1.0)
    kalman: KalmanState
    egarch: EGARCHResult
    mcts: MCTSResult
    source: SignalSource
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reasoning: str = ""
    agent_signals: Optional[Dict[str, Any]] = None  # V6.0: Multi-agent signals


class BacktestMetrics(BaseModel):
    """Comprehensive backtest metrics."""
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    avg_trade_pnl: float
    profit_factor: float
    equity_curve: Optional[List[float]] = None
    sortino_ratio: Optional[float] = None  # V6.0
    calmar_ratio: Optional[float] = None  # V6.0
    max_consecutive_wins: Optional[int] = None  # V6.0
    max_consecutive_losses: Optional[int] = None  # V6.0


class BacktestResponse(BaseModel):
    """Backtest response with metrics and signals."""
    pair: str
    metrics: BacktestMetrics
    signals: List[TradeSignal] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)  # V6.0


class ScanResponse(BaseModel):
    """Scan response with signals and errors."""
    scan_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signals: List[TradeSignal]
    errors: List[str] = Field(default_factory=list)
    summary: Optional[Dict[str, Any]] = None  # V6.0: Quick summary stats


class ExecutionResponse(BaseModel):
    """Execution result."""
    status: str
    action: ActionType
    pair: str
    details: str = ""
    order_ids: List[str] = Field(default_factory=list)
    simulated: bool = True  # V6.0: Explicit dry_run indicator
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HealthResponse(BaseModel):
    """System health status."""
    status: str = "ok"
    version: str = "8.0.0-institutional"
    uptime_seconds: float = 0.0
    modules: Dict[str, bool] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class AgentState(BaseModel):
    """V6.0: Agent state for monitoring."""
    agent_id: str
    role: AgentRole
    status: Literal["active", "idle", "error", "stopped"]
    last_update: datetime
    decisions_made: int
    successful_trades: int
    failed_trades: int
    total_pnl: float
    win_rate: float = 0.0


class RiskMetrics(BaseModel):
    """V6.0: Portfolio risk metrics."""
    total_exposure: float
    daily_pnl: float
    daily_trades: int
    max_daily_trades: int
    active_positions: int
    crisis_mode: bool
    drawdown: float
    var_95: Optional[float] = None
    correlation_matrix: Optional[Dict[str, Dict[str, float]]] = None
