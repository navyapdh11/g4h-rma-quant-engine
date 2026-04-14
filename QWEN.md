# G4H-RMA Quant Engine — Project Context

## Overview
G4H-RMA Quant Engine is a quantitative trading system built on Kalman Filter + EGARCH + MCTS (Monte Carlo Tree Search) with a multi-agent architecture. It supports pairs trading across equities and crypto with 6 broker/exchange connections.

**Location:** `/root/.openclaw/workspace/tools/g4h_quant_engine/`
**API:** `localhost:8000`
**Dashboard:** `http://localhost:8000/`
**Swagger Docs:** `http://localhost:8000/docs`

---

## Architecture

### Core Models
- **Kalman Filter** (`core/kalman.py`): Multivariate Kalman with persistent state per pair, z-score signal generation, eigenvalue-based divergence detection
- **EGARCH** (`core/egarch.py`): EGARCH(1,1) volatility modeling with regime detection (LOW/NORMAL/ELEVATED/CRISIS), volatility scaling
- **MCTS** (`core/mcts.py`): Monte Carlo Tree Search for expected value calculation, adaptive iterations, parallel search, Kelly criterion position sizing
- **Risk Manager** (`core/risk.py`): VaR/CVaR, drawdown circuit breaker, position sizing, stop-loss/take-profit
- **Semantic Cache** (`core/semantic_cache.py`): Intelligent caching with per-source TTLs
- **Sentiment** (`core/sentiment.py`): News sentiment analysis via Finnhub/NewsAPI/GNews
- **Persistence** (`core/persistence.py`): SQLite-backed trade/position tracking

### Multi-Agent System (5 Agents)
- **Scout** (`agents/trading_agents.py`): Scans pairs for opportunities using Kalman z-scores
- **Analyst** (`agents/trading_agents.py`): Deep analysis combining MCTS EV, EGARCH regime, Kalman z-score
- **Trader** (`agents/trading_agents.py`): Execution optimization — timing, position sizing, vol adjustment
- **Risk** (`agents/trading_agents.py`): Risk limits enforcement — daily trade cap, position notional, crisis halt
- **Sentinel** (`agents/trading_agents.py`): Black swan detection — flash crashes, vol spikes, liquidity crises

### Broker Connections (6 Providers)
Managed via `core/connections.py` unified ConnectionManager:
- **Alpaca** — US equities (paper + live)
- **Binance** — Crypto spot + futures
- **Bybit** — Crypto spot + derivatives
- **Futu/Moomoo** — HK/US/CN equities
- **IBKR** — Global multi-asset via TWS/Gateway
- **Tiger** — US/HK equities

### API Layer (`api_layer/`)
Unified abstraction for market data and execution:
- `MarketDataFactory` — yfinance (equities/ETFs), CCXT (crypto), Alpaca Data
- `ExecutionFactory` — Alpaca (paper/live), Binance, Bybit, Futu, IBKR, Tiger
- Abstract base classes: `MarketDataProvider`, `ExecutionProvider`

### REST API (`api/app.py`)
FastAPI server with:
- Thread-safe rate limiting (asyncio lock)
- CORS middleware
- WebSocket streaming for real-time data
- Kalman filter state persistence per pair
- SQLite trade persistence
- Input validation (Pydantic V2)
- Comprehensive error handling

### Dashboard (`static/`)
- `dashboard.html` — Legacy dashboard
- `dashboard-agents.html` — Agent-centric UI with 7 tabs: Dashboard, Agents, Auto-Trading, Scanner, Connections, P&L, Guide

---

## Configuration

**Central config:** `config.py` — all magic numbers live here as dataclasses:
- `KalmanConfig`: warmup_steps=30, eigenvalue_check_interval=10, divergence_threshold=50
- `EGARCHConfig`: lookback=756 days, ewma_lambda=0.94, trading_days=252
- `MCTSConfig`: iterations=800, parallel_workers=4, kelly_fraction=0.25, regime_shift_probability=0.05
- `RiskConfig`: max_daily_trades=10, max_position_notional=25000, max_drawdown_halt=0.10
- `APIConfig`: port=8000, websocket_enabled, rate_limit=60/min
- `AgentConfig`: z_threshold=1.5, strong_threshold=2.0, consensus_threshold=0.6
- `UniverseConfig`: 15 equity pairs (AAPL/MSFT, NVDA/AMD, etc.), 2 crypto pairs (BTC/ETH, SOL/ADA)
- `DataConfig`: circuit_breaker_threshold=5, per-source TTLs

**Environment:** `.env` file (never committed) with Alpaca keys, optional News API keys

---

## Key Commands

### Start
```bash
cd /root/.openclaw/workspace/tools/g4h_quant_engine
source venv/bin/activate
nohup python main.py > logs/g4h-quant-engine.log 2>&1 &
```

### Stop
```bash
pkill -f "python main.py"
```

### CLI Modes
```bash
python main.py --scan      # CLI pair scanner
python main.py --backtest  # CLI backtest (SPY/QQQ)
python main.py --theory    # Math documentation
python main.py --validate  # Config validation
```

### Autodeploy
```bash
./autodeploy.sh            # Start with crash recovery + daily 23:00 restart
./stop-autodeploy.sh       # Stop autodeploy cron + process
./status.sh                # Check engine status
./watcher.sh               # Watch engine logs
```

### Systemd (if installed)
```bash
sudo systemctl start g4h-quant-engine
sudo systemctl status g4h-quant-engine
journalctl -u g4h-quant-engine -f
```

### Docker (if available)
```bash
docker-compose up -d --build
docker-compose logs -f
```

---

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — API server, CLI modes |
| `config.py` | Central configuration (all dataclasses) |
| `api/app.py` | FastAPI REST API (1384 lines) |
| `core/kalman.py` | Multivariate Kalman filter with persistence |
| `core/egarch.py` | EGARCH(1,1) volatility model |
| `core/mcts.py` | Monte Carlo Tree Search engine |
| `core/risk.py` | Risk manager with VaR/CVaR |
| `core/connections.py` | Unified broker connection manager |
| `core/persistence.py` | SQLite trade/position tracking |
| `agents/trading_agents.py` | 5 specialized trading agents |
| `api_layer/factory.py` | Market data + execution factories |
| `api_layer/alpaca_api.py` | Alpaca execution provider |
| `data/fetcher.py` | Data fetching (yfinance, CCXT) |
| `static/dashboard-agents.html` | Agent-centric web dashboard |
| `backtest.py` | Backtest engine with dollar PnL |
| `models.py` | Pydantic models for API |
| `autodeploy.sh` | Production deployment script |
| `.env.example` | Environment template |
| `requirements.txt` | Python dependencies |

---

## Version History
- **V10.0**: Configurable VaR confidence, drawdown circuit breaker reset, position flip support, news sentiment, yfinance timeout protection
- **V8.0**: Parallel MCTS, Kelly criterion, WebSocket streaming, multi-timeframe analysis, advanced risk metrics, performance metrics dashboard
- **V7.0**: Kalman state persistence, SQLite persistence, thread-safe rate limiting, reproducible MCTS seeding, WebSocket message size limits
- **V6.x**: Multi-agent system (5 agents), risk management, circuit breakers, 6 broker connections
- **V5.x**: Base system with Kalman + EGARCH + MCTS, Alpaca + CCXT

---

## Trading Strategy
1. **Kalman Filter** processes price pairs → generates spread, z-score, innovation variance
2. **EGARCH** analyzes historical returns → classifies volatility regime
3. **MCTS** simulates future spread paths → computes expected value for each action
4. **Agents** aggregate signals: Scout (z-score scan) → Analyst (deep analysis) → Trader (execution optimization) → Risk (approve/reject) → Sentinel (black swan watch)
5. **Execution** via configured broker with partial-fill rollback protection
