"""
FastAPI Application — Production REST API V6.0
===============================================
Enhanced with:
  - Input validation and sanitization
  - Rate limiting
  - Comprehensive error handling
  - Enhanced monitoring endpoints
"""
from __future__ import annotations
import asyncio
import time
import uuid
import logging
import re
from typing import List
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from config import settings
from models import (
    ActionType, AssetPair, BacktestRequest, BacktestResponse,
    ExecuteRequest, ExecutionResponse, HealthResponse,
    KalmanState, MCTSResult, ScanRequest, ScanResponse,
    SignalSource, TradeSignal, RiskMetrics,
)
from core.kalman import MultivariateKalmanFilter
from core.egarch import EGARCHVolatilityModel
from core.mcts import MCTSEngine
from core.risk import RiskManager
from data.fetcher import DataFetcher
from execution.alpaca import AlpacaExecutor
from core.connections import connection_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("g4h_api")
_start_time = time.time()

app = FastAPI(
    title="G4H-RMA Quant Engine",
    description="Enterprise Kalman + EGARCH + MCTS Pairs Trading V6.0",
    version="6.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS with optional restriction
cors_origins = settings.api.cors_origins if not settings.api.enable_cors_restriction else ["http://localhost:8000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
import os
static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
app.mount("/static", StaticFiles(directory=static_path), name="static")

# Include agents router
from agents.api import router as agents_router
app.include_router(agents_router)

# Initialize components
fetcher = DataFetcher(settings.data)
risk_mgr = RiskManager(settings.risk)
executor = AlpacaExecutor(settings.execution)
egarch_model = EGARCHVolatilityModel(settings.egarch)
mcts_engine = MCTSEngine(settings.mcts)

# Rate limiting state
_rate_limit_state: dict = {}


def _check_rate_limit(client_ip: str = "default") -> bool:
    """Simple rate limiting."""
    now = time.time()
    minute_ago = now - 60
    
    if client_ip not in _rate_limit_state:
        _rate_limit_state[client_ip] = []
    
    # Remove old requests
    _rate_limit_state[client_ip] = [t for t in _rate_limit_state[client_ip] if t > minute_ago]
    
    if len(_rate_limit_state[client_ip]) >= settings.api.rate_limit_per_minute:
        return False
    
    _rate_limit_state[client_ip].append(now)
    return True


async def _analyze_pair(base, quote, mcts_iters=800,
                       source=SignalSource.KALMAN_MCTS) -> TradeSignal:
    """Analyze a pair with comprehensive error handling."""
    try:
        df = await fetcher.get_yfinance(base, quote, settings.data.default_period)
        if df is None or len(df) < 60:
            raise HTTPException(503, f"Insufficient data for {base}/{quote}")
        
        pa, pb = df[base], df[quote]
        kf = MultivariateKalmanFilter(settings.kalman)
        snapshot = None
        
        for i in range(len(pa)):
            snapshot = kf.step(float(pa.iloc[i]), float(pb.iloc[i]))
        
        if snapshot is None or snapshot.is_divergent:
            raise HTTPException(500, "Kalman filter produced invalid output")
        
        kalman_state = KalmanState(
            beta=snapshot.beta, alpha=snapshot.alpha,
            spread=snapshot.spread, innovation_var=snapshot.innovation_var,
            pure_z_score=snapshot.pure_z_score, converged=snapshot.converged,
        )
        
        egarch_result = egarch_model.analyze(pa, cache_key=base)
        vol_scale = egarch_model.get_vol_scale(egarch_result.regime)
        mcts_result = mcts_engine.search(snapshot.spread, snapshot.innovation_var, vol_scale)
        
        confidence = min(abs(snapshot.pure_z_score) / 3.0, 1.0) * (
            1.0 if mcts_result.action != ActionType.HOLD else 0.3
        )
        
        parts = [
            f"Kalman Z={snapshot.pure_z_score:.2f} (b={snapshot.beta:.3f})",
            f"EGARCH vol={egarch_result.annualized_vol:.1%} [{egarch_result.regime.value}]",
            f"MCTS -> {mcts_result.action.value} (EV={mcts_result.expected_value:.3f})",
        ]
        if egarch_result.leverage_gamma is not None:
            parts.append(f"Leverage g={egarch_result.leverage_gamma:.4f}")
        
        return TradeSignal(
            pair=f"{base}/{quote}", action=mcts_result.action,
            confidence=round(confidence, 3), kalman=kalman_state,
            egarch=egarch_result, mcts=mcts_result, source=source,
            reasoning=" | ".join(parts),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analysis error for {base}/{quote}: {e}")
        raise HTTPException(500, f"Analysis failed: {str(e)}")


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        version="6.0.0-enterprise",
        uptime_seconds=round(time.time() - _start_time, 1),
        modules={
            "kalman": True, "egarch": True, "mcts": True,
            "alpaca": executor.is_live, "fetcher": True,
        },
    )


@app.get("/")
async def root():
    """Serve the multi-agent dashboard GUI."""
    return FileResponse(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "dashboard-agents.html")
    )


@app.post("/api/v1/scan", response_model=ScanResponse)
async def scan_pairs(request: ScanRequest):
    if not _check_rate_limit():
        raise HTTPException(429, "Rate limit exceeded")
    
    scan_id = uuid.uuid4().hex[:8]
    tasks = [
        _analyze_pair(p.base, p.quote, request.mcts_iterations)
        for p in request.pairs
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    signals, errors = [], []
    for i, res in enumerate(results):
        ps = f"{request.pairs[i].base}/{request.pairs[i].quote}"
        if isinstance(res, Exception):
            errors.append(f"{ps}: {str(res)}")
        elif isinstance(res, TradeSignal):
            signals.append(res)
    
    # Add summary
    summary = {
        "total_pairs": len(request.pairs),
        "successful": len(signals),
        "failed": len(errors),
        "buy_signals": sum(1 for s in signals if s.action in [ActionType.BUY, ActionType.LONG_SPREAD]),
        "sell_signals": sum(1 for s in signals if s.action in [ActionType.SELL, ActionType.SHORT_SPREAD]),
    }
    
    return ScanResponse(scan_id=scan_id, signals=signals, errors=errors, summary=summary)


@app.get("/api/v1/scan/{pair}", response_model=TradeSignal)
async def scan_single(pair: str, iterations: int = Query(800, ge=100, le=5000)):
    # Validate pair format
    if not re.match(r"^[A-Z0-9\-\./]+_[A-Z0-9\-\./]+$", pair):
        raise HTTPException(400, "Invalid pair format. Use SYMBOL1_SYMBOL2")
    
    base, quote = pair.split("_", 1)
    return await _analyze_pair(base, quote, iterations)


@app.post("/api/v1/execute", response_model=ExecutionResponse)
async def execute_signal(request: ExecuteRequest):
    if not _check_rate_limit():
        raise HTTPException(429, "Rate limit exceeded")
    
    try:
        signal = await _analyze_pair(request.base, request.quote)
        
        if signal.action == ActionType.HOLD:
            return ExecutionResponse(
                status="no_action", action=ActionType.HOLD,
                pair=signal.pair, details="Current signal is HOLD.",
                simulated=request.dry_run,
            )
        
        approved, reason = risk_mgr.approve(signal)
        if not approved:
            return ExecutionResponse(
                status="rejected", action=signal.action,
                pair=signal.pair, details=reason,
                simulated=request.dry_run,
            )
        
        df = await fetcher.get_yfinance(request.base, request.quote)
        price_base = float(df[request.base].iloc[-1])
        price_quote = float(df[request.quote].iloc[-1])
        
        action = request.action if request.action != ActionType.HOLD else signal.action
        result = executor.execute(
            request.base, request.quote, action,
            price_base, price_quote, signal.kalman.beta,
            qty_base=request.qty, dry_run=request.dry_run,
        )
        result.simulated = request.dry_run
        
        if result.status in ("executed", "simulated"):
            risk_mgr.record_trade(
                signal.pair, signal.action,
                entry_z=signal.kalman.pure_z_score,
                entry_spread=signal.kalman.spread,
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Execution error: {e}")
        return ExecutionResponse(
            status="error", action=ActionType.HOLD,
            pair=f"{request.base}/{request.quote}",
            details=str(e), simulated=request.dry_run,
        )


@app.get("/api/v1/history")
async def get_history():
    return executor.get_history()


@app.get("/api/v1/positions")
async def get_positions():
    return {
        "active_pairs": list(risk_mgr._active_positions.keys()),
        "positions": risk_mgr.get_active_positions(),
    }


@app.get("/api/v1/account")
async def get_account():
    """Get Alpaca account information."""
    account_info = await executor.get_account()

    if "error" in account_info:
        return {
            "status": "SIMULATION",
            "paper_trading": True,
            "message": "API keys not configured — running in simulation mode",
            "setup_instructions": {
                "step1": "Get API keys from https://app.alpaca.markets/paper",
                "step2": "Set environment variables:",
                "variables": {
                    "APCA_API_KEY_ID": "your_key_here",
                    "APCA_API_SECRET_KEY": "your_secret_here",
                    "APCA_API_BASE_URL": "https://paper-api.alpaca.markets"
                }
            }
        }

    return {
        "status": account_info.get("status", "UNKNOWN"),
        "account_number": account_info.get("account_number", ""),
        "paper_trading": account_info.get("paper", True),
        "currency": account_info.get("currency", "USD"),
        "cash": account_info.get("cash", 0),
        "portfolio_value": account_info.get("portfolio_value", 0),
        "buying_power": account_info.get("buying_power", 0),
        "pattern_day_trader": account_info.get("pattern_day_trader", False),
        "trading_blocked": account_info.get("trading_blocked", True),
        "transfers_blocked": account_info.get("transfers_blocked", True),
    }


@app.get("/api/v1/risk/metrics", response_model=RiskMetrics)
async def get_risk_metrics():
    """V6.0: Get comprehensive risk metrics."""
    metrics = risk_mgr.get_risk_metrics()
    return RiskMetrics(**metrics)


@app.get("/api/v1/theory")
async def get_theory():
    return {
        "title": "G4H-RMA Mathematical Foundations V6.0",
        "kalman_filter": {
            "state": "x = [beta, alpha]^T",
            "transition": "x_k = x_{k-1} + w  (F=I, w~N(0,Q))",
            "observation": "z = Price_A,  H = [Price_B, 1]",
            "predict": "x^- = x,  P^- = P + Q",
            "innovation": "y = z - H*x^-",
            "S": "H*P^-*H' + R",
            "gain": "K = P^-*H'*S^-1",
            "update": "x^+ = x^- + K*y",
            "joseph_form": "P^+ = (I-KH)*P^-*('I-KH)' + K*R*K'",
            "pure_z": "Z = y / sqrt(S)",
        },
        "egarch": {
            "model": "EGARCH(1,1)",
            "spec": "log(sigma^2_t) = w + b*log(sigma^2_{t-1}) + a*|z_{t-1}| + g*z_{t-1}",
            "leverage": "g < 0 => negative shocks increase future vol",
        },
        "mcts": {
            "algorithm": "UCB1 Tree Search",
            "selection": "UCB1: Q/N + c*sqrt(ln(N_parent)/N)",
            "simulation": "OU: dz = lambda*(mu-z)*dt + sigma*sqrt(dt)*N(0,1)",
            "policy": "Most-visited child (robust)",
            "vol_integration": "sigma = sqrt(S) * vol_scale(regime)",
        },
    }


@app.get("/api/v1/backtest", response_model=BacktestResponse)
async def run_backtest_api(
    base: str, quote: str,
    start: str = "2020-01-01",
    end: str = None,
    initial_capital: float = 100000.0,
    z_entry: float = 2.0,
):
    """Run backtest via API."""
    from backtest import run_backtest

    req = BacktestRequest(
        base=base, quote=quote, start=start, end=end,
        initial_capital=initial_capital, z_entry=z_entry,
    )

    try:
        result = run_backtest(req)
        return result
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        raise HTTPException(500, f"Backtest failed: {str(e)}")


# ─── Connection Management API V6.1 ───────────────────────────────────

class ConnectionUpdateRequest(BaseModel):
    """Update connection configuration."""
    enabled: Optional[bool] = None
    paper_trading: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None


@app.get("/api/v1/connections")
async def list_connections():
    """List all broker connection configurations."""
    return {"connections": connection_manager.get_all_configs()}


@app.get("/api/v1/connections/{provider}")
async def get_connection(provider: str):
    """Get single connection config."""
    cfg = connection_manager.get_config(provider)
    if not cfg:
        raise HTTPException(404, f"Provider '{provider}' not found")
    return cfg


@app.post("/api/v1/connections/{provider}")
async def update_connection(provider: str, req: ConnectionUpdateRequest):
    """Update connection configuration."""
    if provider not in connection_manager.PROVIDER_META:
        raise HTTPException(404, f"Provider '{provider}' not found")

    connection_manager.update_config(
        provider=provider,
        enabled=req.enabled,
        paper_trading=req.paper_trading,
        config=req.config,
    )
    return {"status": "updated", "provider": provider}


@app.post("/api/v1/connections/{provider}/test")
async def test_connection(provider: str):
    """Test connection to a broker."""
    if provider not in connection_manager.PROVIDER_META:
        raise HTTPException(404, f"Provider '{provider}' not found")

    result = await connection_manager.test_connection(provider)
    return result


@app.post("/api/v1/connections/test-all")
async def test_all_connections():
    """Test all enabled connections."""
    results = await connection_manager.test_all()
    return {"results": results}


@app.post("/api/v1/connections/{provider}/disconnect")
async def disconnect_provider(provider: str):
    """Disconnect a broker."""
    connection_manager.disconnect(provider)
    return {"status": "disconnected", "provider": provider}


@app.get("/api/v1/connections/active")
async def list_active_connections():
    """List active (connected) providers."""
    active = connection_manager.get_active_providers()
    return {"active": list(active.keys()), "count": len(active)}
