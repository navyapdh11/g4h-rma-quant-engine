# G4H-RMA Quant Engine V7.0 — Deployment Guide

## 📋 Table of Contents

1. [Quick Start](#quick-start)
2. [Deployment Options](#deployment-options)
3. [Docker Deployment](#docker-deployment)
4. [Systemd Deployment](#systemd-deployment)
5. [Manual Deployment](#manual-deployment)
6. [Configuration](#configuration)
7. [Monitoring](#monitoring)
8. [Troubleshooting](#troubleshooting)
9. [V7.0 Changelog](#v70-changelog)

---

## 🚀 Quick Start

```bash
cd /root/.openclaw/workspace/tools/g4h_quant_engine

# 1. Install dependencies
./deploy.sh --install

# 2. Configure environment
cp .env.example .env
nano .env  # Add your API keys

# 3. Validate configuration
./deploy.sh --validate

# 4. Start the service
./deploy.sh --start

# 5. Check status
./deploy.sh --status

# 6. View logs
./deploy.sh --logs
```

---

## 📦 Deployment Options

| Method | Best For | Pros | Cons |
|--------|----------|------|------|
| **Docker** | Production, isolation | Clean, reproducible, easy rollback | Requires Docker |
| **Systemd** | Linux servers, auto-restart | Native Linux, auto-recovery | Root required |
| **Manual** | Development, testing | Simple, no setup | No auto-recovery |

---

## 🐳 Docker Deployment

### Prerequisites

- Docker 20.10+
- Docker Compose 2.0+ (or docker-compose 1.29+)

### Steps

```bash
# 1. Configure environment
cp .env.example .env
nano .env  # Add your API keys

# 2. Build and start
./deploy.sh --docker

# OR manually:
docker-compose up -d --build

# 3. Check status
docker ps | grep g4h-rma-quant-engine

# 4. View logs
docker-compose logs -f

# 5. Stop
docker-compose down
```

### Docker Commands

```bash
# View container logs
docker-compose logs -f quant-engine

# Restart container
docker-compose restart

# Rebuild image
docker-compose build --no-cache

# Access container shell
docker-compose exec quant-engine /bin/bash

# Check health
curl http://localhost:8000/health
```

---

## 🔧 Systemd Deployment

### Prerequisites

- Linux with systemd (Ubuntu 18.04+, CentOS 7+, Debian 9+)
- Root/sudo access
- Python 3.8+

### Steps

```bash
# 1. Configure environment
cd /root/.openclaw/workspace/tools/g4h_quant_engine
cp .env.example .env
nano .env  # Add your API keys

# 2. Deploy (requires root)
sudo ./deploy.sh --systemd

# 3. Check status
sudo systemctl status g4h-quant-engine

# 4. View logs
journalctl -u g4h-quant-engine -f
```

### Systemd Commands

```bash
# Start service
sudo systemctl start g4h-quant-engine

# Stop service
sudo systemctl stop g4h-quant-engine

# Restart service
sudo systemctl restart g4h-quant-engine

# Enable auto-start on boot
sudo systemctl enable g4h-quant-engine

# Disable auto-start
sudo systemctl disable g4h-quant-engine

# View status
sudo systemctl status g4h-quant-engine

# View logs (real-time)
journalctl -u g4h-quant-engine -f

# View logs (last 100 lines)
journalctl -u g4h-quant-engine -n 100
```

---

## 🖥️ Manual Deployment

### Steps

```bash
# 1. Navigate to project
cd /root/.openclaw/workspace/tools/g4h_quant_engine

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
nano .env

# 5. Validate configuration
python main.py --validate

# 6. Start server
python main.py

# Or run in background
nohup python main.py > logs/startup.log 2>&1 &

# 7. Check health
curl http://localhost:8000/health
```

---

## ⚙️ Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `APCA_API_KEY_ID` | Alpaca API key | (required for trading) |
| `APCA_API_SECRET_KEY` | Alpaca API secret | (required for trading) |
| `APCA_API_BASE_URL` | Alpaca API URL | `https://paper-api.alpaca.markets` |
| `API_HOST` | API bind host | `0.0.0.0` |
| `API_PORT` | API port | `8000` |
| `API_WORKERS` | Number of workers | `1` |
| `MAX_DAILY_TRADES` | Max trades per day | `10` |
| `MAX_POSITION_NOTIONAL` | Max position size (USD) | `25000` |

### Configuration Files

| File | Purpose |
|------|---------|
| `.env` | Environment variables (DO NOT COMMIT) |
| `.env.example` | Template for .env |
| `.env.production` | Production configuration template |
| `config.py` | Application configuration |
| `g4h-quant-engine.service` | Systemd service file |
| `docker-compose.yml` | Docker Compose configuration |
| `Dockerfile` | Docker image definition |

---

## 📊 Monitoring

### Health Check Script

```bash
# Quick health check
./monitor.sh

# Continuous monitoring
./monitor.sh --watch

# Generate report
./monitor.sh --report

# Test alerts
./monitor.sh --alert
```

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | System health status |
| `GET /api/v1/risk/metrics` | Risk metrics |
| `GET /api/v1/account` | Account information |
| `GET /api/v1/positions` | Active positions |
| `GET /api/v1/history` | Trade history |

### Health Check URL

```bash
# Simple health check
curl http://localhost:8000/health

# Detailed check with jq
curl http://localhost:8000/health | jq

# Check in loop
watch -n 5 'curl -s http://localhost:8000/health | jq'
```

---

## 🔧 Troubleshooting

### Common Issues

#### 1. Port Already in Use

```bash
# Check what's using port 8000
lsof -i :8000

# Kill the process
kill -9 $(lsof -t -i :8000)

# Or use a different port
export API_PORT=8001
python main.py
```

#### 2. API Keys Not Configured

```bash
# Check .env file
cat .env

# Verify keys are set
echo $APCA_API_KEY_ID
echo $APCA_API_SECRET_KEY
```

#### 3. Virtual Environment Issues

```bash
# Remove and recreate venv
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### 4. Systemd Service Fails

```bash
# Check service status
sudo systemctl status g4h-quant-engine

# View detailed logs
journalctl -u g4h-quant-engine -n 100 --no-pager

# Test configuration
sudo systemd-analyze verify /etc/systemd/system/g4h-quant-engine.service

# Reload systemd
sudo systemctl daemon-reload
sudo systemctl restart g4h-quant-engine
```

#### 5. Docker Container Won't Start

```bash
# Check container logs
docker-compose logs quant-engine

# Rebuild image
docker-compose build --no-cache

# Remove and recreate
docker-compose down
docker-compose up -d
```

#### 6. High Memory Usage

```bash
# Check memory
free -h
docker stats  # For Docker

# Reduce MCTS iterations
# Edit .env or config.py:
# MCTS_ITERATIONS=400  # Lower = less memory
```

### Log Locations

| Deployment | Log Location |
|------------|--------------|
| Manual | `logs/startup.log` |
| Systemd | `journalctl -u g4h-quant-engine` |
| Docker | `docker-compose logs` |

---

## 📈 Performance Tuning

### Production Recommendations

1. **Increase Workers** (for high traffic)
   ```bash
   # In .env
   API_WORKERS=4
   
   # Or use gunicorn
   pip install gunicorn
   gunicorn -w 4 -k uvicorn.workers.UvicornWorker api.app:app
   ```

2. **Enable Caching** (Redis)
   ```yaml
   # docker-compose.yml
   services:
     redis:
       image: redis:7-alpine
   ```

3. **Set Up Reverse Proxy** (Nginx)
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;
       
       location / {
           proxy_pass http://localhost:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```

4. **Enable HTTPS** (Let's Encrypt)
   ```bash
   sudo certbot --nginx -d your-domain.com
   ```

---

## 🔐 Security Checklist

- [ ] API keys configured and secured
- [ ] `.env` file not committed to git
- [ ] CORS restricted to known origins
- [ ] Rate limiting enabled
- [ ] Firewall configured (only ports 80/443/8000 open)
- [ ] Regular security updates
- [ ] Logs monitored for suspicious activity
- [ ] Backup strategy in place

---

## 📞 Support

For issues and questions:
- Check logs: `./deploy.sh --logs`
- Run diagnostics: `./monitor.sh --report`
- Review documentation: `http://localhost:8000/docs`

---

## 📝 V7.0 Changelog

### Critical Fixes

1. **Kalman Filter State Persistence** (CRITICAL)
   - **Before**: A new `MultivariateKalmanFilter` was created for EVERY API call, resetting to priors and producing zeroed z-scores for the first 30 steps
   - **After**: Persistent Kalman filter cache per pair (`_kalman_cache: Dict[str, MultivariateKalmanFilter]`). Filters now maintain state across requests, producing accurate z-scores immediately

2. **Thread-Safe Rate Limiting** (CRITICAL)
   - **Before**: `_rate_limit_state` dict was not thread-safe; concurrent requests with `workers > 1` could bypass rate limits
   - **After**: Asyncio lock (`_rate_limit_lock`) protects rate limit state. `_check_rate_limit` is now async and uses client IP from request context

3. **Auto-Trading Engine Live Market Data** (CRITICAL)
   - **Before**: Trading engine used hardcoded placeholder data (`SPY/QQQ` at fixed prices 450/400)
   - **After**: Fetches live market data from configured equity pairs via `DataFetcher`. Scans top 5 pairs per tick with proper error handling and minimal fallback

4. **Global Executor Initialization** (CRITICAL)
   - **Before**: `_engine` global singleton created without executor parameter, making executor always `None`
   - **After**: `get_trading_engine()` now imports and initializes `AlpacaExecutor`, passing it to `RealTimeTradingEngine`. Falls back to `None` with warning if init fails

### Significant Enhancements

5. **SQLite Persistence** (NEW)
   - New `core/persistence.py` module with thread-safe SQLite backend
   - Tables: `trades`, `positions`, `daily_stats` with automatic creation
   - Methods: `record_trade`, `open_position`, `update_position`, `close_position`, `get_trade_history`, `get_open_positions`, `get_pnl_summary`
   - WAL journal mode, busy timeout 5s, per-thread connections via `threading.local`
   - Circuit breaker tracking and daily PnL aggregation

6. **Partial-Fill Rollback** (NEW)
   - Alpaca executor now detects partial fills and automatically rolls back
   - `_rollback_legs()` submits opposite orders for any filled legs before error
   - Rollback status reported in error response (`success_N_legs_reversed`, `failed_partial_rollback`, `not_attempted`)
   - Quantity validation before order submission (rejects qty <= 0)

7. **Reproducible MCTS Seeding** (NEW)
   - Added `seed` parameter to `MCTSConfig` (default `None` for non-deterministic)
   - `MCTSEngine.set_seed(seed)` and `get_seed()` methods for runtime control
   - Backtests now use fixed seed (42) for reproducibility

8. **WebSocket Message Size Limit** (SECURITY)
   - Added 1MB message size limit to WebSocket endpoint (`MAX_WS_MESSAGE_SIZE = 1_048_576`)
   - Prevents memory exhaustion attacks
   - Returns error message and continues listening on oversized messages

9. **Version Consistency**
   - All version strings updated: V5.0/V6.0 → V7.0
   - API version: `7.0.0-next-gen`
   - Dashboard: Updated header, title, footer
   - Documentation: Updated DEPLOYMENT.md, QUICKSTART.md references

### Configuration Changes

- `MCTSConfig.seed`: New optional field for reproducible backtesting
- Health endpoint now reports `persistence: True` module flag
- Theory endpoint updated to V7.0

### Migration Notes

- **No breaking changes**: All existing API endpoints remain compatible
- **SQLite database**: Automatically created at `data/engine.db` on first use
- **Kalman cache**: Existing behavior improved; no config changes needed
- **Rate limiting**: Now properly enforced per-client-IP with async lock
