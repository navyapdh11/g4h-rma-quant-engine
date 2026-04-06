"""
G4H-RMA Configuration — Central, validated, typed.
All magic numbers live here, not scattered across modules.
V10.0: Added trading_days, per-source TTL, MCTS regime shift, Kalman eigenvalue interval.
V8.0: Added Kelly criterion, parallel MCTS, multi-timeframe, advanced risk.
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime


@dataclass(frozen=True)
class KalmanConfig:
    """Kalman Filter configuration with validated parameters."""
    initial_beta: float = 1.0
    initial_alpha: float = 0.0
    initial_covariance: float = 100.0
    process_noise_beta: float = 1e-5
    process_noise_alpha: float = 1e-4
    measurement_noise: float = 1.0
    warmup_steps: int = 30
    max_eigenvalue: float = 1e6
    # V10.0: Compute eigenvalues every N steps (1 = every step, 10 = every 10 steps)
    eigenvalue_check_interval: int = 10
    # V10.0: Divergence detection
    divergence_threshold: float = 50.0
    divergence_history: int = 50
    variance_healthy_threshold: float = 10.0
    eigenvalue_floor: float = 1e-10

    def __post_init__(self):
        if self.warmup_steps < 10:
            raise ValueError("warmup_steps must be >= 10")
        if self.initial_covariance <= 0:
            raise ValueError("initial_covariance must be positive")


@dataclass(frozen=True)
class EGARCHConfig:
    """EGARCH volatility model configuration."""
    p: int = 1
    o: int = 1
    q: int = 1
    dist: str = "normal"
    lookback_days: int = 756
    high_vol_annual: float = 0.40
    cache_ttl_seconds: int = 3600
    # V10.0: EWMA lambda (RiskMetrics standard = 0.94, crypto = 0.90)
    ewma_lambda: float = 0.94
    # V10.0: Trading days per year for annualization (252 equities, 365 crypto)
    trading_days: int = 252
    # V10.0: Optimizer tolerance
    optimizer_tol: float = 1e-6

    def __post_init__(self):
        if self.lookback_days < 252:
            raise ValueError("EGARCH lookback should be at least 1 year (252 days)")


@dataclass(frozen=True)
class MCTSConfig:
    """Monte Carlo Tree Search configuration."""
    iterations: int = 800
    rollout_steps: int = 5
    mean_reversion_speed: float = 5.0
    exploration_constant: float = 1.414
    min_spread_magnitude: float = 1.0
    min_ev_threshold: float = 0.05
    vol_scale_low: float = 1.0
    vol_scale_mid: float = 3.0
    vol_scale_high: float = 8.0
    vol_mid_threshold: float = 0.25
    vol_high_threshold: float = 0.40
    adaptive_iterations: bool = True
    seed: Optional[int] = None
    # V8.0: Parallel search
    parallel_workers: int = 4  # Number of parallel search threads
    dynamic_depth: bool = True  # Dynamic tree depth based on volatility
    max_depth: int = 20  # Maximum tree depth
    # V8.0: Kelly criterion
    kelly_fraction: float = 0.25  # Fraction of Kelly to use (conservative)
    kelly_enabled: bool = True
    # V10.0: Configurable regime shift probability (was hardcoded 0.05)
    regime_shift_probability: float = 0.05
    # V10.0: Depth penalty factor (was hardcoded 0.1)
    depth_penalty_factor: float = 0.1
    # V10.0: Parallel worker timeout (was hardcoded 30s)
    parallel_worker_timeout: float = 30.0

    def __post_init__(self):
        if self.iterations < 100:
            raise ValueError("MCTS iterations must be >= 100")
        if self.iterations > 5000:
            raise ValueError("MCTS iterations must be <= 5000")
        if self.parallel_workers < 1:
            raise ValueError("Parallel workers must be >= 1")


@dataclass
class ExecutionConfig:
    """Execution configuration with security masking."""
    alpaca_key: str = field(default_factory=lambda: os.environ.get("APCA_API_KEY_ID", ""))
    alpaca_secret: str = field(default_factory=lambda: os.environ.get("APCA_API_SECRET_KEY", ""))
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    ccxt_exchange: str = "binance"
    default_qty_base: int = 10
    max_position_notional: float = 25000.0
    max_daily_loss: float = 5000.0  # V6.0: Daily loss limit
    stop_loss_pct: float = 0.02  # V6.0: Per-trade stop loss

    def __repr__(self):
        """Mask sensitive fields in logs."""
        key_masked = f"***{self.alpaca_key[-4:]}" if self.alpaca_key else "NONE"
        secret_masked = "***REDACTED***" if self.alpaca_secret else "NONE"
        return (f"ExecutionConfig(alpaca_key='{key_masked}', "
                f"alpaca_secret='{secret_masked}', "
                f"base_url='{self.alpaca_base_url}')")


@dataclass(frozen=True)
class DataConfig:
    """Data fetching configuration with circuit breaker."""
    default_period: str = "2y"
    cache_ttl_seconds: int = 14400
    max_cache_mb: int = 50
    fetch_timeout_seconds: int = 30
    retry_max: int = 3
    retry_backoff: float = 1.5
    circuit_breaker_threshold: int = 5  # V6.0: Failures before opening
    circuit_breaker_timeout: int = 60  # V6.0: Seconds before retry
    # V10.0: Per-source cache TTLs (seconds)
    cache_ttl_equity: int = 3600    # 1 hour for daily equity data
    cache_ttl_crypto: int = 300     # 5 min for intraday crypto data
    cache_ttl_generated: int = 86400  # 24 hours for synthetic data


@dataclass(frozen=True)
class RiskConfig:
    """V10.0: Comprehensive risk management configuration."""
    max_daily_trades: int = 10
    max_position_notional: float = 25000.0
    max_portfolio_exposure: float = 100000.0
    max_correlation: float = 0.8
    crisis_halt: bool = True
    stop_loss_enabled: bool = True
    stop_loss_z: float = 5.0
    take_profit_z: float = 0.5
    position_size_vol_scale: bool = True
    max_drawdown_halt: float = 0.10
    min_confidence_threshold: float = 0.3  # Minimum signal confidence to approve trade
    # V8.0: Advanced risk metrics
    var_confidence: float = 0.95  # VaR confidence level
    cvar_enabled: bool = True  # Conditional VaR (Expected Shortfall)
    kelly_position_sizing: bool = True  # Use Kelly criterion
    max_leverage: float = 2.0  # Maximum portfolio leverage
    stress_test_enabled: bool = True  # Daily stress tests
    correlation_lookback: int = 60  # Days for correlation calculation
    # V8.0: Multi-timeframe analysis
    timeframes: list = None  # Multiple timeframes for analysis
    primary_timeframe: str = "1d"
    secondary_timeframe: str = "4h"

    def __post_init__(self):
        if self.timeframes is None:
            object.__setattr__(self, 'timeframes', ["1d", "4h", "1h"])


@dataclass(frozen=True)
class APIConfig:
    """API server configuration."""
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
    request_timeout_seconds: int = 60
    rate_limit_per_minute: int = 60
    enable_cors_restriction: bool = False
    # V8.0: WebSocket streaming
    websocket_enabled: bool = True
    websocket_max_clients: int = 50
    websocket_ping_interval: int = 30  # seconds
    max_ws_message_size: int = 1_048_576  # 1MB


@dataclass(frozen=True)
class AgentConfig:
    """V6.0: Multi-agent system configuration."""
    scout_z_threshold: float = 1.5
    scout_strong_threshold: float = 2.0
    analyst_ev_weight: float = 0.5
    analyst_z_weight: float = 0.5
    trader_vol_adjustment: bool = True
    trader_confidence_decay: float = 0.9
    risk_confidence_min: float = 0.3
    sentinel_flash_crash_threshold: float = -0.05
    sentinel_vol_spike_threshold: float = 3.0
    consensus_threshold: float = 0.6
    min_agents_for_consensus: int = 2


@dataclass(frozen=True)
class UniverseConfig:
    """Trading universe configuration."""
    equity_pairs: List[Tuple[str, str]] = field(default_factory=lambda: [
        ("AAPL", "MSFT"), ("NVDA", "AMD"), ("GOOGL", "META"), ("AMZN", "GOOGL"),
        ("JPM", "BAC"), ("V", "MA"), ("GS", "MS"), ("XOM", "CVX"),
        ("KO", "PEP"), ("WMT", "COST"), ("PG", "KMB"), ("MCD", "YUM"),
        ("PFE", "JNJ"), ("LLY", "ABBV"), ("DAL", "UAL"),
    ])
    crypto_pairs_ccxt: List[Tuple[str, str]] = field(default_factory=lambda: [
        ("BTC/USDT", "ETH/USDT"), ("SOL/USDT", "ADA/USDT"),
    ])
    crypto_pairs_yf: List[Tuple[str, str]] = field(default_factory=lambda: [
        ("BTC-USD", "ETH-USD"), ("SOL-USD", "ADA-USD"),
    ])


@dataclass(frozen=True)
class AppConfig:
    """Master application configuration with validation."""
    kalman: KalmanConfig = KalmanConfig()
    egarch: EGARCHConfig = EGARCHConfig()
    mcts: MCTSConfig = MCTSConfig()
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    data: DataConfig = DataConfig()
    api: APIConfig = APIConfig()
    universe: UniverseConfig = UniverseConfig()
    risk: RiskConfig = RiskConfig()  # V6.0
    agent: AgentConfig = AgentConfig()  # V6.0
    
    def validate_all(self) -> List[str]:
        """Validate all configuration sections. Returns list of errors."""
        errors = []
        if self.mcts.iterations < 100 or self.mcts.iterations > 5000:
            errors.append("MCTS iterations must be between 100 and 5000")
        if self.egarch.lookback_days < 252:
            errors.append("EGARCH lookback must be at least 252 days")
        if self.risk.max_daily_trades < 1:
            errors.append("Max daily trades must be at least 1")
        return errors


settings = AppConfig()
