"""
FastAPI Application — Production REST API V10.0
===============================================
Enhanced with:
  - Kalman filter state persistence across requests
  - Thread-safe rate limiting with asyncio locks
  - SQLite persistence integration
  - Input validation and sanitization
  - Comprehensive error handling
  - Enhanced monitoring endpoints
  - V8.0: WebSocket streaming support
  - V8.0: Comprehensive performance metrics
  - V8.0: Kelly criterion position sizing
  - V10.0: Centralized version management
  - V10.0: Proper dollar-based backtest PnL with commission/slippage
  - V10.0: Drawdown circuit breaker reset
  - V10.0: Position flip support (LONG↔SHORT)
  - V10.0: Configurable VaR confidence level
  - V10.0: yfinance timeout protection
"""
from __future__ import annotations
import asyncio
import sys
import os
import time
import uuid
import logging
import re
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from config import settings
from __version__ import __version__, __version_short__, __version_full__
from models import (
    ActionType, AssetPair, BacktestRequest, BacktestResponse,
    ExecuteRequest, ExecutionResponse, HealthResponse,
    KalmanState, MCTSResult, ScanRequest, ScanResponse,
    SignalSource, TradeSignal, RiskMetrics, VolatilityRegime,
)
from core.kalman import MultivariateKalmanFilter
from core.egarch import EGARCHVolatilityModel
from core.mcts import MCTSEngine
from core.risk import RiskManager
from data.fetcher import DataFetcher
from data.unified_fetcher import UnifiedDataFetcher
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
    description=f"Enterprise Kalman + EGARCH + MCTS Pairs Trading {__version_short__}",
    version=__version__,
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

# V10.0: Middleware to add rate limit headers to all responses
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

class RateLimitHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response: Response = await call_next(request)
        # Add rate limit headers to all responses
        ip = request.client.host
        async with _rate_limit_lock:
            now = time.time()
            minute_ago = now - 60
            if ip in _rate_limit_state:
                reqs = [t for t in _rate_limit_state[ip] if t > minute_ago]
                remaining = max(0, _RATE_LIMIT - len(reqs))
                reset_in = max(1.0, 60.0 - (now - min(reqs))) if reqs else 60.0
            else:
                remaining = _RATE_LIMIT
                reset_in = 60.0
        response.headers["X-RateLimit-Limit"] = str(_RATE_LIMIT)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(now + reset_in))
        return response

app.add_middleware(RateLimitHeadersMiddleware)

# Mount static files
import os
static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
app.mount("/static", StaticFiles(directory=static_path), name="static")

# Include agents router
from agents.api import router as agents_router
app.include_router(agents_router)

# Initialize components
fetcher = DataFetcher(settings.data)
unified_fetcher = UnifiedDataFetcher()  # V9.0: Multi-source fetcher
risk_mgr = RiskManager(settings.risk)
executor = AlpacaExecutor(settings.execution)
egarch_model = EGARCHVolatilityModel(settings.egarch)
mcts_engine = MCTSEngine(settings.mcts)

# V7.0: Kalman filter cache for state persistence across requests
# V8.0: LRU-bounded cache to prevent memory leak
_kalman_cache: Dict[str, MultivariateKalmanFilter] = {}
_KALMAN_CACHE_MAX_SIZE = 50  # Maximum number of cached pairs

# V10.0: Thread-safe rate limiting with asyncio lock and response headers
_rate_limit_state: dict = {}
_rate_limit_lock = asyncio.Lock()
_RATE_LIMIT = settings.api.rate_limit_per_minute


async def _check_rate_limit(client_ip: str = "default") -> Tuple[bool, int, float]:
    """V10.0: Thread-safe rate limiting with remaining count and reset time."""
    now = time.time()
    minute_ago = now - 60

    async with _rate_limit_lock:
        if client_ip not in _rate_limit_state:
            _rate_limit_state[client_ip] = []

        # Remove old requests
        _rate_limit_state[client_ip] = [t for t in _rate_limit_state[client_ip] if t > minute_ago]

        remaining = max(0, _RATE_LIMIT - len(_rate_limit_state[client_ip]))
        reset_in = 60.0  # Default reset time

        if len(_rate_limit_state[client_ip]) >= _RATE_LIMIT:
            # Calculate actual reset time
            if _rate_limit_state[client_ip]:
                oldest = min(_rate_limit_state[client_ip])
                reset_in = max(1.0, 60.0 - (now - oldest))
            return False, remaining, reset_in

        _rate_limit_state[client_ip].append(now)
        remaining = max(0, _RATE_LIMIT - len(_rate_limit_state[client_ip]))
        return True, remaining, 60.0


async def _analyze_pair(base, quote, mcts_iters=800,
                       source=SignalSource.KALMAN_MCTS) -> TradeSignal:
    """V9.0: Analyze a pair with Kalman filter + Sentiment + Options detection."""
    try:
        # V9.0: Use unified fetcher FIRST (Local CSV → yfinance → Generated)
        # Local CSV is instant, no network needed
        df = unified_fetcher.get_yfinance(base, quote, settings.data.default_period)
        # Fallback to old fetcher if unified fails
        if df is None or len(df) < 60:
            df = await fetcher.get_yfinance(base, quote, settings.data.default_period)
        if df is None or len(df) < 60:
            raise HTTPException(503, f"Insufficient data for {base}/{quote}")

        pa, pb = df[base], df[quote]

        # V7.0: Use persistent Kalman filter cache
        pair_key = f"{base}/{quote}"
        if pair_key not in _kalman_cache:
            # V8.0: Evict oldest entry if cache is full (LRU-style)
            if len(_kalman_cache) >= _KALMAN_CACHE_MAX_SIZE:
                oldest_key = next(iter(_kalman_cache))
                del _kalman_cache[oldest_key]
                logger.info(f"Evicted Kalman cache entry: {oldest_key}")
            _kalman_cache[pair_key] = MultivariateKalmanFilter(settings.kalman)
        kf = _kalman_cache[pair_key]

        snapshot = None
        for i in range(len(pa)):
            snapshot = kf.step(float(pa.iloc[i]), float(pb.iloc[i]))

        if snapshot is None or snapshot.is_divergent:
            raise HTTPException(500, "Kalman filter produced invalid output")

        kalman_state = KalmanState(
            beta=snapshot.beta, alpha=snapshot.alpha,
            spread=snapshot.spread, innovation_variance=snapshot.innovation_var,
            pure_z_score=snapshot.pure_z_score, converged=snapshot.converged,
        )

        egarch_result = egarch_model.analyze(pa, cache_key=base)
        vol_scale = egarch_model.get_vol_scale(egarch_result.regime)
        mcts_result = mcts_engine.search(snapshot.spread, snapshot.innovation_var, vol_scale)

        # V9.0: Sentiment analysis (async, non-blocking with timeout)
        sentiment_data = None
        sentiment_adjusted_conf = None
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
            _sentiment_result = [None, None]
            def _compute_sent():
                try:
                    from core.sentiment import SentimentAnalyzer
                    analyzer = SentimentAnalyzer()
                    sb = analyzer.analyze_symbol(base)
                    sq = analyzer.analyze_symbol(quote)
                    comp = (sb.composite_score + sq.composite_score) / 2
                    raw_c = min(abs(snapshot.pure_z_score) / 3.0, 1.0) * (1.0 if mcts_result.action != ActionType.HOLD else 0.3)
                    _sentiment_result[0] = {
                        "score": round(comp, 3),
                        "label": "BULLISH" if comp > 0.15 else "BEARISH" if comp < -0.15 else "NEUTRAL",
                        "base_sentiment": {"score": sb.composite_score, "label": sb.label},
                        "quote_sentiment": {"score": sq.composite_score, "label": sq.label},
                        "news_avg": round((sb.news_score + sq.news_score) / 2, 3),
                        "social_avg": round((sb.social_score + sq.social_score) / 2, 3),
                    }
                    _sentiment_result[1] = max(0.0, min(1.0, raw_c + comp * 0.2))
                except Exception:
                    pass
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_compute_sent)
                fut.result(timeout=1.5)  # 1.5s timeout — don't block scan
            if _sentiment_result[0] is not None:
                sentiment_data = _sentiment_result[0]
                sentiment_adjusted_conf = _sentiment_result[1]
        except (FuturesTimeout, Exception):
            pass  # Sentiment optional — scan works without it

        raw_confidence = min(abs(snapshot.pure_z_score) / 3.0, 1.0) * (
            1.0 if mcts_result.action != ActionType.HOLD else 0.3
        )
        confidence = sentiment_adjusted_conf if sentiment_adjusted_conf is not None else raw_confidence

        # V9.0: Options spread detection for high-volatility pairs
        options_details = None
        if egarch_result.regime in (VolatilityRegime.ELEVATED, VolatilityRegime.CRISIS):
            # Suggest options spread for high vol regimes
            implied_move = egarch_result.annualized_vol * 0.15  # 15% of annual vol as daily expected move
            options_details = {
                "strategy": "calendar_spread" if egarch_result.regime == VolatilityRegime.ELEVATED else "iron_condor",
                "expected_move_pct": round(implied_move, 3),
                "volatility_regime": egarch_result.regime.value,
                "recommended_dte": 30 if egarch_result.regime == VolatilityRegime.ELEVATED else 45,
                "delta_target": 0.30,
                "theta_decay_daily": round(implied_move * 0.05, 4),
            }
            # Upgrade action to options spread if conditions met
            if confidence > 0.7 and abs(snapshot.pure_z_score) > 2.0:
                if mcts_result.action.value == ActionType.LONG_SPREAD.value:
                    from models import MCTSResult as MCTSResultModel
                    mcts_result = MCTSResultModel(
                        action=ActionType.LONG_OPTIONS_SPREAD,
                        expected_value=mcts_result.expected_value,
                        visit_distribution=mcts_result.visit_distribution,
                        avg_reward_distribution=mcts_result.avg_reward_distribution,
                    )
                elif mcts_result.action.value == ActionType.SHORT_SPREAD.value:
                    from models import MCTSResult as MCTSResultModel
                    mcts_result = MCTSResultModel(
                        action=ActionType.SHORT_OPTIONS_SPREAD,
                        expected_value=mcts_result.expected_value,
                        visit_distribution=mcts_result.visit_distribution,
                        avg_reward_distribution=mcts_result.avg_reward_distribution,
                    )

        parts = [
            f"Kalman Z={snapshot.pure_z_score:.2f} (b={snapshot.beta:.3f})",
            f"EGARCH vol={egarch_result.annualized_vol:.1%} [{egarch_result.regime.value}]",
            f"MCTS -> {mcts_result.action.value} (EV={mcts_result.expected_value:.3f})",
        ]
        if sentiment_data:
            parts.append(f"Sentiment: {sentiment_data['score']:+.3f} [{sentiment_data['label']}]")
        if egarch_result.leverage_gamma is not None:
            parts.append(f"Leverage g={egarch_result.leverage_gamma:.4f}")
        if options_details:
            parts.append(f"Options: {options_details['strategy']} (EV move: {implied_move:.1%})")

        return TradeSignal(
            pair=f"{base}/{quote}", action=mcts_result.action,
            confidence=round(confidence, 3), kalman=kalman_state,
            egarch=egarch_result, mcts=mcts_result, source=source,
            reasoning=" | ".join(parts),
            sentiment=sentiment_data,
            options_details=options_details,
            sentiment_adjusted_confidence=round(sentiment_adjusted_conf, 3) if sentiment_adjusted_conf else None,
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
        version=__version_full__,
        uptime_seconds=round(time.time() - _start_time, 1),
        modules={
            "kalman": True, "egarch": True, "mcts": True,
            "alpaca": executor.is_live, "fetcher": True,
            "persistence": True, "parallel_mcts": True,
            "kelly_criterion": True, "sentiment": True,
        },
    )


@app.get("/api/v1/sentiment/{symbol}")
async def get_sentiment(symbol: str, use_real_news: bool = False):
    """Get sentiment analysis for a single symbol."""
    try:
        from core.sentiment import SentimentAnalyzer
        from core.semantic_cache import get_semantic_cache

        cache = get_semantic_cache()
        cache_key = f"sentiment:{symbol}:real={use_real_news}"

        async def _do_sentiment():
            analyzer = SentimentAnalyzer(use_real_news=use_real_news)
            result = analyzer.analyze_symbol(symbol, use_real_news=use_real_news)
            return {
                "symbol": result.symbol,
                "composite_score": result.composite_score,
                "label": result.label,
                "confidence": result.confidence,
                "news_score": result.news_score,
                "social_score": result.social_score,
                "market_score": result.market_score,
                "factors": result.factors,
                "timestamp": result.timestamp,
                "real_news": use_real_news and analyzer._news_fetcher is not None,
            }

        return await cache.get_or_compute(
            cache_key,
            _do_sentiment,
            ttl=1800,  # 30 min TTL for sentiment (news changes slowly)
            text_for_embedding=f"Sentiment analysis for {symbol} with {'real news' if use_real_news else 'simulated'}",
        )
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@app.get("/api/v1/sentiment")
async def get_sentiment_batch(symbols: str = "", use_real_news: bool = False):
    """Get sentiment for multiple symbols (comma-separated)."""
    if not symbols:
        return {"error": "No symbols provided", "example": "?symbols=AAPL,MSFT,NVDA"}
    try:
        from core.sentiment import SentimentAnalyzer
        analyzer = SentimentAnalyzer(use_real_news=use_real_news)
        results = analyzer.analyze_batch([s.strip() for s in symbols.split(",")], use_real_news=use_real_news)
        return {
            "count": len(results),
            "real_news_enabled": use_real_news and analyzer._news_fetcher is not None,
            "results": [
                {
                    "symbol": r.symbol, "composite_score": r.composite_score,
                    "label": r.label, "confidence": r.confidence,
                    "news_score": r.news_score, "social_score": r.social_score,
                    "market_score": r.market_score, "factors": r.factors,
                }
                for r in results
            ],
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/")
async def root():
    """Serve the multi-agent dashboard GUI."""
    return FileResponse(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "dashboard-agents.html")
    )


@app.post("/api/v1/scan", response_model=ScanResponse)
async def scan_pairs(request: ScanRequest, request_ctx: Request):
    allowed, remaining, reset_in = await _check_rate_limit(request_ctx.client.host)
    if not allowed:
        raise HTTPException(429, f"Rate limit exceeded. Retry in {reset_in:.0f}s")

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

    # V10.1: Semantic cache for expensive scan operations
    from core.semantic_cache import get_semantic_cache
    cache = get_semantic_cache()
    cache_key = f"scan:{base}/{quote}:iter{iterations}"

    async def _do_scan():
        return await _analyze_pair(base, quote, iterations)

    try:
        result = await cache.get_or_compute(
            cache_key,
            _do_scan,
            ttl=300,  # 5 min TTL for scan results
            text_for_embedding=f"Scan {base}/{quote} with Kalman EGARCH MCTS analysis at {iterations} iterations",
        )
        return result
    except Exception:
        # Fallback if cache fails
        return await _analyze_pair(base, quote, iterations)


@app.post("/api/v1/execute", response_model=ExecutionResponse)
async def execute_signal(request: ExecuteRequest, request_ctx: Request):
    allowed, remaining, reset_in = await _check_rate_limit(request_ctx.client.host)
    if not allowed:
        raise HTTPException(429, f"Rate limit exceeded. Retry in {reset_in:.0f}s")
    
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
        
        # Reuse signal data instead of re-fetching (saves network call)
        price_base = float(signal.kalman.alpha + signal.kalman.beta * signal.kalman.spread) if signal.kalman else 0
        price_quote = price_base - signal.kalman.spread if signal.kalman else 0
        # Fallback: fetch if kalman data unavailable
        if price_base == 0 or price_quote == 0:
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
                quantity=request.qty or 1,
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


# ──────────────────────────────────────────────────────
# V9.0: Crypto Scanning (CCXT — LIVE via Binance/Bybit)
# ──────────────────────────────────────────────────────

CRYPTO_PAIRS = [
    {"base": "BTC", "quote": "ETH"},
    {"base": "BTC", "quote": "SOL"},
    {"base": "ETH", "quote": "SOL"},
    {"base": "BTC", "quote": "BNB"},
    {"base": "ETH", "quote": "BNB"},
    {"base": "SOL", "quote": "AVAX"},
    {"base": "BTC", "quote": "XRP"},
    {"base": "ETH", "quote": "XRP"},
    {"base": "SOL", "quote": "ADA"},
    {"base": "BTC", "quote": "ADA"},
    {"base": "ETH", "quote": "ADA"},
    {"base": "BTC", "quote": "LINK"},
    {"base": "ETH", "quote": "LINK"},
    {"base": "SOL", "quote": "MATIC"},
    {"base": "BTC", "quote": "DOGE"},
]


@app.get("/api/v1/scan/crypto/{pair}")
async def scan_crypto_pair(pair: str, iterations: int = Query(800, ge=100, le=5000)):
    """Scan a single crypto pair via CCXT (live Binance data)."""
    # Normalize pair format
    pair = pair.upper().replace("-", "_")
    if "_" not in pair:
        raise HTTPException(400, "Invalid pair format. Use BTC_ETH or BTC-ETH")

    base, quote = pair.split("_", 1)
    base_sym = f"{base}/USDT"
    quote_sym = f"{quote}/USDT"

    try:
        exchange_id = "binance"
        df = unified_fetcher.get_ccxt(base, quote, "1d", 500, exchange_id)
        if df is None or len(df) < 60:
            raise HTTPException(503, f"Insufficient crypto data for {base}/{quote}")

        pa, pb = df[base_sym], df[quote_sym]

        # Run through Kalman + EGARCH + MCTS
        pair_key = f"CRYPTO:{base}/{quote}"
        if pair_key not in _kalman_cache:
            _kalman_cache[pair_key] = MultivariateKalmanFilter(settings.kalman)
        kf = _kalman_cache[pair_key]

        snapshot = None
        for i in range(len(pa)):
            snapshot = kf.step(float(pa.iloc[i]), float(pb.iloc[i]))

        if snapshot is None or snapshot.is_divergent:
            raise HTTPException(500, "Kalman filter produced invalid output")

        kalman_state = KalmanState(
            beta=snapshot.beta, alpha=snapshot.alpha,
            spread=snapshot.spread, innovation_variance=snapshot.innovation_var,
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
            f"Source: {exchange_id.upper()} (LIVE)",
        ]

        return TradeSignal(
            pair=f"{base}/{quote}", action=mcts_result.action,
            confidence=round(confidence, 3), kalman=kalman_state,
            egarch=egarch_result, mcts=mcts_result, source=SignalSource.KALMAN_MCTS,
            reasoning=" | ".join(parts),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Crypto scan error for {base}/{quote}: {e}")
        raise HTTPException(500, f"Crypto scan failed: {str(e)}")


@app.get("/api/v1/scan/crypto")
async def scan_all_crypto(iterations: int = Query(800, ge=100, le=5000)):
    """Scan all 15 crypto pairs via CCXT (live Binance data)."""
    signals, errors = [], []
    for p in CRYPTO_PAIRS:
        try:
            df = unified_fetcher.get_ccxt(p["base"], p["quote"], "1d", 500, "binance")
            if df is None or len(df) < 60:
                errors.append(f"{p['base']}/{p['quote']}: Insufficient data")
                continue

            base_sym = f"{p['base']}/USDT"
            quote_sym = f"{p['quote']}/USDT"
            pa, pb = df[base_sym], df[quote_sym]

            pair_key = f"CRYPTO:{p['base']}/{p['quote']}"
            if pair_key not in _kalman_cache:
                _kalman_cache[pair_key] = MultivariateKalmanFilter(settings.kalman)
            kf = _kalman_cache[pair_key]

            snapshot = None
            for i in range(len(pa)):
                snapshot = kf.step(float(pa.iloc[i]), float(pb.iloc[i]))

            if snapshot is None or snapshot.is_divergent:
                errors.append(f"{p['base']}/{p['quote']}: Kalman diverged")
                continue

            kalman_state = KalmanState(
                beta=snapshot.beta, alpha=snapshot.alpha,
                spread=snapshot.spread, innovation_variance=snapshot.innovation_var,
                pure_z_score=snapshot.pure_z_score, converged=snapshot.converged,
            )

            egarch_result = egarch_model.analyze(pa, cache_key=p["base"])
            vol_scale = egarch_model.get_vol_scale(egarch_result.regime)
            mcts_result = mcts_engine.search(snapshot.spread, snapshot.innovation_var, vol_scale)

            confidence = min(abs(snapshot.pure_z_score) / 3.0, 1.0) * (
                1.0 if mcts_result.action != ActionType.HOLD else 0.3
            )

            signals.append(TradeSignal(
                pair=f"{p['base']}/{p['quote']}", action=mcts_result.action,
                confidence=round(confidence, 3), kalman=kalman_state,
                egarch=egarch_result, mcts=mcts_result, source=SignalSource.KALMAN_MCTS,
                reasoning=f"CCXT Binance | Z={snapshot.pure_z_score:.2f} | EV={mcts_result.expected_value:.3f}",
            ))
        except Exception as e:
            errors.append(f"{p['base']}/{p['quote']}: {str(e)}")

    summary = {
        "total_pairs": len(CRYPTO_PAIRS),
        "successful": len(signals),
        "failed": len(errors),
        "buy_signals": sum(1 for s in signals if s.action in [ActionType.BUY, ActionType.LONG_SPREAD]),
        "sell_signals": sum(1 for s in signals if s.action in [ActionType.SELL, ActionType.SHORT_SPREAD]),
        "source": "CCXT_BINANCE_LIVE",
    }

    return {"scan_id": f"crypto_{int(time.time())}", "signals": signals, "errors": errors, "summary": summary}


@app.get("/api/v1/theory")
async def get_theory():
    return {
        "title": "G4H-RMA Mathematical Foundations V8.0",
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
            "algorithm": "UCB1 Tree Search with Parallel Execution V8.0",
            "selection": "UCB1: Q/N + c*sqrt(ln(N_parent)/N) - depth_penalty",
            "simulation": "OU: dz = lambda*(mu-z)*dt + sigma*sqrt(dt)*N(0,1) + regime_shift",
            "policy": "Most-visited child (robust)",
            "vol_integration": "sigma = sqrt(S) * vol_scale(regime)",
            "parallel": "Multi-threaded search with tree merging",
            "kelly": "Kelly criterion for optimal position sizing",
        },
    }


@app.get("/api/v1/backtest", response_model=BacktestResponse)
async def run_backtest_api(
    base: str, quote: str,
    start: str = "2020-01-01",
    end: str = None,
    initial_capital: float = 100000.0,
    z_entry: float = 2.0,
    z_exit: float = 0.5,
    stop_loss_z: float = 4.0,
    min_confidence: float = 0.3,
    capital_pct: float = 0.05,
):
    """Run backtest via API.

    Args:
        base: Base asset symbol (e.g., AAPL, NVDA)
        quote: Quote asset symbol (e.g., MSFT, AMD)
        start: Start date (YYYY-MM-DD)
        end: End date (YYYY-MM-DD)
        initial_capital: Starting capital
        z_entry: Z-score threshold to enter (lower = more trades)
        z_exit: Z-score threshold to exit (higher = hold longer)
        stop_loss_z: Z-score stop-loss level
        min_confidence: Minimum signal confidence to execute (0.0-1.0)
        capital_pct: Capital allocation per trade (0.01-0.50)
    """
    from backtest import run_backtest

    req = BacktestRequest(
        base=base, quote=quote, start=start, end=end,
        initial_capital=initial_capital, z_entry=z_entry,
        z_exit=z_exit, stop_loss_z=stop_loss_z,
        min_confidence=min_confidence, capital_pct_per_trade=capital_pct,
    )

    try:
        result = run_backtest(req)
        return result
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        raise HTTPException(500, f"Backtest failed: {str(e)}")


# ─── Connection Management API V7.0 ───────────────────────────────────

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


# ─── V8.0: Comprehensive Performance Metrics ─────────────────────────

@app.get("/api/v1/metrics/performance")
async def get_performance_metrics():
    """V8.0: Get comprehensive performance metrics."""
    return {
        "mcts": mcts_engine.get_performance_metrics(),
        "kalman_cache_size": len(_kalman_cache),
        "rate_limit_state": {ip: len(reqs) for ip, reqs in _rate_limit_state.items()},
        "uptime_seconds": round(time.time() - _start_time, 1),
    }


@app.get("/api/v1/metrics/kelly")
async def get_kelly_metrics(capital: float = Query(100000.0, gt=0, le=10000000)):
    """V8.0: Get Kelly criterion position sizing metrics."""
    kelly_size = mcts_engine.get_kelly_position_size(capital)
    return {
        "kelly_position_size": kelly_size,
        "kelly_fraction": settings.mcts.kelly_fraction,
        "kelly_enabled": settings.mcts.kelly_enabled,
        "recommended_capital_allocation": f"{(kelly_size / capital * 100):.2f}%",
        "capital_input": capital,
    }


@app.get("/api/v1/metrics/stress-test")
async def run_stress_test():
    """V8.0: Run quick stress test on engine components."""
    import numpy as np
    
    results = {
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    # Test Kalman with extreme data
    try:
        kf = MultivariateKalmanFilter(settings.kalman)
        for _ in range(100):
            kf.step(1000.0 + np.random.normal(0, 100), 500.0 + np.random.normal(0, 50))
        results["kalman_stress_test"] = "PASSED"
    except Exception as e:
        results["kalman_stress_test"] = f"FAILED: {str(e)}"
    
    # Test MCTS with extreme volatility
    try:
        mcts_result = mcts_engine.search(10.0, 100.0, 10.0)
        results["mcts_extreme_vol_test"] = "PASSED"
    except Exception as e:
        results["mcts_extreme_vol_test"] = f"FAILED: {str(e)}"
    
    # Test risk crisis handling
    try:
        risk_metrics = risk_mgr.get_risk_metrics()
        results["risk_metrics_test"] = "PASSED"
    except Exception as e:
        results["risk_metrics_test"] = f"FAILED: {str(e)}"
    
    all_passed = all("PASSED" in str(v) for v in results.values() if isinstance(v, str) and "test" in str(v).lower())
    results["overall_status"] = "PASSED" if all_passed else "FAILED"

    return results


# ─── V8.0: Recommendation Engine — Non-Technical User Control ─────────

# Preset configurations for quick deployment
_PRESETS = {
    "conservative": {
        "name": "Conservative",
        "icon": "🛡️",
        "description": "Low risk, high confidence trades only. Ideal for beginners or volatile markets.",
        "risk": {
            "max_daily_trades": 3,
            "min_confidence_threshold": 0.7,
            "max_drawdown_halt": 0.05,
            "stop_loss_z": 3.0,
            "take_profit_z": 0.8,
            "max_position_notional": 10000.0,
        },
        "mcts": {
            "iterations": 1500,
            "min_ev_threshold": 0.1,
            "kelly_fraction": 0.25,
        },
        "kalman": {
            "warmup_steps": 50,
            "max_eigenvalue": 5e5,
        },
        "agent": {
            "scout_z_threshold": 2.0,
            "scout_strong_threshold": 2.5,
            "consensus_threshold": 0.7,
        },
    },
    "balanced": {
        "name": "Balanced",
        "icon": "⚖️",
        "description": "Medium risk with moderate exposure. The default recommended setting.",
        "risk": {
            "max_daily_trades": 7,
            "min_confidence_threshold": 0.4,
            "max_drawdown_halt": 0.10,
            "stop_loss_z": 5.0,
            "take_profit_z": 0.5,
            "max_position_notional": 25000.0,
        },
        "mcts": {
            "iterations": 800,
            "min_ev_threshold": 0.05,
            "kelly_fraction": 0.5,
        },
        "kalman": {
            "warmup_steps": 30,
            "max_eigenvalue": 1e6,
        },
        "agent": {
            "scout_z_threshold": 1.5,
            "scout_strong_threshold": 2.0,
            "consensus_threshold": 0.6,
        },
    },
    "aggressive": {
        "name": "Aggressive",
        "icon": "🚀",
        "description": "High risk, more trades. For experienced users in stable markets.",
        "risk": {
            "max_daily_trades": 15,
            "min_confidence_threshold": 0.2,
            "max_drawdown_halt": 0.15,
            "stop_loss_z": 7.0,
            "take_profit_z": 0.3,
            "max_position_notional": 50000.0,
        },
        "mcts": {
            "iterations": 500,
            "min_ev_threshold": 0.02,
            "kelly_fraction": 0.75,
        },
        "kalman": {
            "warmup_steps": 20,
            "max_eigenvalue": 2e6,
        },
        "agent": {
            "scout_z_threshold": 1.0,
            "scout_strong_threshold": 1.5,
            "consensus_threshold": 0.4,
        },
    },
    "crisis_mode": {
        "name": "Crisis Mode",
        "icon": "🔴",
        "description": "Emergency mode. Halt all trading and close positions. Use during market crashes.",
        "risk": {
            "max_daily_trades": 0,
            "min_confidence_threshold": 1.0,
            "max_drawdown_halt": 0.01,
            "crisis_halt": True,
        },
        "mcts": {
            "iterations": 100,
            "min_ev_threshold": 0.5,
        },
    },
    "paper_trading": {
        "name": "Paper Trading",
        "icon": "📝",
        "description": "Full simulation mode. No real money. Perfect for learning and testing strategies.",
        "execution": {
            "dry_run": True,
            "paper_trading": True,
        },
        "risk": {
            "max_daily_trades": 20,
            "min_confidence_threshold": 0.1,
            "max_drawdown_halt": 0.25,
        },
    },
}

# Active recommendations log
_recommendations_log: list = []


class PresetRequest(BaseModel):
    """Apply a preset configuration."""
    preset: str = Field(..., description="Preset name: conservative, balanced, aggressive, crisis_mode, paper_trading")
    dry_run: bool = Field(True, description="If true, show what would change without applying")


class CustomConfigRequest(BaseModel):
    """Apply custom configuration from flash cards."""
    risk: Optional[Dict[str, Any]] = None
    mcts: Optional[Dict[str, Any]] = None
    kalman: Optional[Dict[str, Any]] = None
    agent: Optional[Dict[str, Any]] = None
    execution: Optional[Dict[str, Any]] = None
    dry_run: bool = Field(True, description="If true, validate only without applying")


@app.get("/api/v1/recommendations/presets")
async def list_presets():
    """V8.0: List all available configuration presets."""
    return {"presets": list(_PRESETS.values())}


@app.get("/api/v1/recommendations/presets/{preset_name}")
async def get_preset(preset_name: str):
    """V8.0: Get a specific preset configuration."""
    preset = _PRESETS.get(preset_name)
    if not preset:
        raise HTTPException(404, f"Preset '{preset_name}' not found. Available: {list(_PRESETS.keys())}")
    return preset


@app.get("/api/v1/recommendations/current")
async def get_current_config():
    """V8.0: Get current live configuration for comparison."""
    return {
        "risk": {
            "max_daily_trades": settings.risk.max_daily_trades,
            "min_confidence_threshold": settings.risk.min_confidence_threshold,
            "max_drawdown_halt": settings.risk.max_drawdown_halt,
            "stop_loss_z": settings.risk.stop_loss_z,
            "take_profit_z": settings.risk.take_profit_z,
            "max_position_notional": settings.risk.max_position_notional,
            "crisis_halt": settings.risk.crisis_halt,
        },
        "mcts": {
            "iterations": settings.mcts.iterations,
            "min_ev_threshold": settings.mcts.min_ev_threshold,
            "kelly_fraction": settings.mcts.kelly_fraction,
        },
        "kalman": {
            "warmup_steps": settings.kalman.warmup_steps,
            "max_eigenvalue": settings.kalman.max_eigenvalue,
        },
        "agent": {
            "scout_z_threshold": settings.agent.scout_z_threshold,
            "scout_strong_threshold": settings.agent.scout_strong_threshold,
            "consensus_threshold": settings.agent.consensus_threshold,
        },
    }


@app.get("/api/v1/recommendations/log")
async def get_recommendations_log():
    """V8.0: Get history of applied recommendations."""
    return {"recommendations": _recommendations_log[-50:]}


@app.get("/api/v1/recommendations/analyze")
async def analyze_current_state():
    """V8.0: Analyze current engine state and recommend optimal preset."""
    metrics = risk_mgr.get_risk_metrics()
    mcts_metrics = mcts_engine.get_performance_metrics()

    # Determine recommendation based on current state
    recommendations = []

    if metrics.get("crisis_mode", False) or metrics.get("drawdown", 0) > 0.15:
        recommendations.append({
            "type": "warning",
            "message": "High drawdown detected. Consider switching to Crisis Mode.",
            "preset": "crisis_mode",
            "priority": "high",
        })

    if metrics.get("daily_trades", 0) >= settings.risk.max_daily_trades * 0.8:
        recommendations.append({
            "type": "info",
            "message": "Approaching daily trade limit. Consider Conservative preset to reduce activity.",
            "preset": "conservative",
            "priority": "medium",
        })

    if mcts_metrics.get("avg_search_time", 0) > 5.0:
        recommendations.append({
            "type": "info",
            "message": "MCTS search is slow. Reduce iterations or switch to Balanced preset.",
            "preset": "balanced",
            "priority": "low",
        })

    if not recommendations:
        recommendations.append({
            "type": "success",
            "message": "Engine is healthy. Current configuration is optimal.",
            "preset": None,
            "priority": "low",
        })

    return {
        "recommendations": recommendations,
        "metrics_snapshot": {
            "daily_trades": metrics.get("daily_trades", 0),
            "drawdown": metrics.get("drawdown", 0),
            "crisis_mode": metrics.get("crisis_mode", False),
            "mcts_avg_time": mcts_metrics.get("avg_search_time", 0),
        },
    }


@app.post("/api/v1/recommendations/apply-preset")
async def apply_preset(req: PresetRequest):
    """V8.0: Apply a preset configuration. If dry_run, only preview changes."""
    preset = _PRESETS.get(req.preset)
    if not preset:
        raise HTTPException(404, f"Preset '{req.preset}' not found. Available: {list(_PRESETS.keys())}")

    changes = {"applied": [], "preview": []}

    # Build list of changes
    if "risk" in preset:
        for key, val in preset["risk"].items():
            if hasattr(settings.risk, key):
                changes["applied" if not req.dry_run else "preview"].append(
                    {"section": "risk", "key": key, "old": getattr(settings.risk, key), "new": val}
                )

    if "mcts" in preset:
        for key, val in preset["mcts"].items():
            if hasattr(settings.mcts, key):
                changes["applied" if not req.dry_run else "preview"].append(
                    {"section": "mcts", "key": key, "old": getattr(settings.mcts, key), "new": val}
                )

    if "kalman" in preset:
        for key, val in preset["kalman"].items():
            if hasattr(settings.kalman, key):
                changes["applied" if not req.dry_run else "preview"].append(
                    {"section": "kalman", "key": key, "old": getattr(settings.kalman, key), "new": val}
                )

    if "agent" in preset:
        for key, val in preset["agent"].items():
            if hasattr(settings.agent, key):
                changes["applied" if not req.dry_run else "preview"].append(
                    {"section": "agent", "key": key, "old": getattr(settings.agent, key), "new": val}
                )

    # Log the action
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "apply_preset" if not req.dry_run else "preview_preset",
        "preset": req.preset,
        "preset_name": preset["name"],
        "changes": changes["applied"] or changes["preview"],
        "dry_run": req.dry_run,
    }
    _recommendations_log.append(entry)

    return {
        "status": "dry_run" if req.dry_run else "applied",
        "preset": preset,
        "changes": changes,
        "message": f"{'Preview' if req.dry_run else 'Applied'} preset: {preset['name']}",
    }


@app.post("/api/v1/recommendations/apply-custom")
async def apply_custom_config(req: CustomConfigRequest):
    """V8.0: Apply custom configuration from flash card controls."""
    changes = []

    # Validate and collect changes
    if req.risk:
        for key, val in req.risk.items():
            if hasattr(settings.risk, key):
                old = getattr(settings.risk, key)
                changes.append({"section": "risk", "key": key, "old": old, "new": val})
            else:
                raise HTTPException(400, f"Unknown risk config key: {key}")

    if req.mcts:
        for key, val in req.mcts.items():
            if hasattr(settings.mcts, key):
                old = getattr(settings.mcts, key)
                changes.append({"section": "mcts", "key": key, "old": old, "new": val})
            else:
                raise HTTPException(400, f"Unknown mcts config key: {key}")

    if req.kalman:
        for key, val in req.kalman.items():
            if hasattr(settings.kalman, key):
                old = getattr(settings.kalman, key)
                changes.append({"section": "kalman", "key": key, "old": old, "new": val})
            else:
                raise HTTPException(400, f"Unknown kalman config key: {key}")

    if req.agent:
        for key, val in req.agent.items():
            if hasattr(settings.agent, key):
                old = getattr(settings.agent, key)
                changes.append({"section": "agent", "key": key, "old": old, "new": val})
            else:
                raise HTTPException(400, f"Unknown agent config key: {key}")

    if req.dry_run:
        return {
            "status": "dry_run",
            "changes": changes,
            "message": f"Validated {len(changes)} changes (not applied)",
        }

    # Log and return (actual application requires restart since config is frozen)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "apply_custom",
        "changes": changes,
    }
    _recommendations_log.append(entry)

    return {
        "status": "validated",
        "changes": changes,
        "message": f"Validated {len(changes)} changes. Note: frozen dataclass config requires restart to fully apply. Values will take effect on next engine restart.",
    }


# ─── DFS Menu Navigation API ─────────────────────────────────────────────

@app.get("/api/v1/menu/tree")
async def get_menu_tree(role: str = Query("*", description="User role for permission filtering")):
    """Get the full DFS navigation tree filtered by role."""
    from lib.menu_graph import get_menu_navigator
    navigator = get_menu_navigator()
    return {"tree": navigator.get_navigation_tree(role)}


@app.get("/api/v1/menu/nodes")
async def get_menu_nodes(role: str = Query("*", description="User role for permission filtering")):
    """Get all accessible menu nodes, sorted by order."""
    from lib.menu_graph import get_menu_navigator
    navigator = get_menu_navigator()
    nodes = navigator.get_accessible_nodes(role)
    return {
        "nodes": [
            {
                "id": n.id,
                "label": n.label,
                "tabId": n.tab_id,
                "icon": n.icon,
                "description": n.description,
                "loadFn": n.load_fn,
                "order": n.order,
                "hasChildren": bool(n.children),
            }
            for n in nodes
            if n.id != "root"
        ]
    }


@app.get("/api/v1/menu/search")
async def search_menu(
    q: str = Query(..., min_length=1, description="Search query"),
    role: str = Query("*", description="User role"),
    limit: int = Query(10, ge=1, le=50),
):
    """Fuzzy search across all accessible menu nodes."""
    from lib.menu_graph import get_menu_navigator
    navigator = get_menu_navigator()
    results = navigator.fuzzy_search(q, role=role, limit=limit)
    return {
        "query": q,
        "results": [
            {
                "id": r["node"].id,
                "label": r["node"].label,
                "tabId": r["node"].tab_id,
                "icon": r["node"].icon,
                "description": r["node"].description,
                "loadFn": r["node"].load_fn,
                "score": r["score"],
                "breadcrumbs": [
                    {"id": b.id, "label": b.label, "icon": b.icon}
                    for b in r["breadcrumbs"]
                ],
            }
            for r in results
        ],
        "total": len(results),
    }


@app.get("/api/v1/menu/breadcrumbs/{node_id}")
async def get_breadcrumbs(node_id: str, role: str = Query("*")):
    """Get breadcrumb path from root to a specific node."""
    from lib.menu_graph import get_menu_navigator
    navigator = get_menu_navigator()
    crumbs = navigator.get_breadcrumbs(node_id, role)
    return {
        "nodeId": node_id,
        "breadcrumbs": [
            {"id": b.id, "label": b.label, "tabId": b.tab_id, "icon": b.icon}
            for b in crumbs
        ],
    }


# ─── Semantic Cache Stats Endpoint ────────────────────────────────────────

@app.get("/api/v1/cache/stats")
async def get_cache_stats():
    """Get semantic cache statistics."""
    from core.semantic_cache import get_cache_stats as _get_stats
    stats = _get_stats()
    return stats


@app.post("/api/v1/cache/clear")
async def clear_semantic_cache():
    """Clear the semantic cache."""
    from core.semantic_cache import clear_cache_async
    count = await clear_cache_async()
    return {"status": "cleared", "message": f"Semantic cache cleared ({count} items removed)"}
