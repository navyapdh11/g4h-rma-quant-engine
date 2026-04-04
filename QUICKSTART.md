# 🚀 G4H-RMA Quant Engine — Quick Start Guide

## ⚡ 3-Minute Setup

### Step 1: Get Alpaca Paper Trading Keys (FREE)

1. Go to **https://app.alpaca.markets/paper**
2. Sign up or log in
3. Click **"View Keys"** in the sidebar
4. Copy your **API Key ID** and **Secret Key**

> 🔒 **Paper trading = Fake money, real market data** — completely safe for testing!

---

### Step 2: Configure Environment

```bash
cd /root/.openclaw/workspace/tools/g4h_quant_engine

# Copy the example file
cp .env.example .env

# Edit with your keys
nano .env
```

**Replace these lines in `.env`:**
```bash
APCA_API_KEY_ID=PKXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX        # ← Your key here
APCA_API_SECRET_KEY=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX  # ← Your secret here
APCA_API_BASE_URL=https://paper-api.alpaca.markets
```

**Save:** `Ctrl+O` → `Enter` → `Ctrl+X`

---

### Step 3: Start the Bot

```bash
# Option A: Use the startup script (recommended)
./start-alpaca.sh

# Option B: Manual start
source venv/bin/activate
python main.py
```

**You should see:**
```
============================================================
  G4H-RMA Quant Engine V5.0 — Enterprise Edition
  Kalman + EGARCH + MCTS + Alpaca + CCXT
============================================================
  API:  http://0.0.0.0:8000
  Docs: http://0.0.0.0:8000/docs
✓ Alpaca Paper Trading connected
```

---

## 📊 Check Account Balance

```bash
curl http://localhost:8000/api/v1/account
```

**Expected output:**
```json
{
  "status": "ACTIVE",
  "account_number": "XXXXX",
  "paper_trading": true,
  "currency": "USD",
  "cash": 100000.00,
  "portfolio_value": 100000.00,
  "buying_power": 200000.00,
  "pattern_day_trader": false,
  "trading_blocked": false
}
```

> 💡 **Alpaca paper accounts start with $100,000 virtual cash**

---

## 🧪 Run a Test Trade (Dry-Run Mode)

**Always test with `dry_run: true` first!**

```bash
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "base": "SPY",
    "quote": "QQQ",
    "action": "LONG_SPREAD",
    "dry_run": true
  }'
```

**Expected response:**
```json
{
  "status": "simulated",
  "action": "LONG_SPREAD",
  "pair": "SPY/QQQ",
  "details": "DRY RUN: BUY 10 SPY @ $450.00, SELL 9.5 QQQ @ $475.00 (beta=1.050, notional~$9,025)"
}
```

---

## 🎯 Execute Real Paper Trade

**Ready to trade with virtual money?**

```bash
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "base": "SPY",
    "quote": "QQQ",
    "action": "LONG_SPREAD",
    "dry_run": false
  }'
```

**Response if successful:**
```json
{
  "status": "executed",
  "action": "LONG_SPREAD",
  "pair": "SPY/QQQ",
  "order_ids": ["order_id_1", "order_id_2"],
  "details": "Filled: BUY 10 SPY + SELL 9.5 QQQ"
}
```

---

## 🌐 Use the Web Dashboard

Open your browser:

**http://localhost:8000/**

Features:
- ✅ Real-time health monitoring
- ✅ Pair scanner with signals
- ✅ One-click trade execution
- ✅ Activity log
- ✅ Mathematical foundations reference

---

## 📚 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web dashboard |
| `/health` | GET | System health check |
| `/api/v1/account` | GET | Account balance |
| `/api/v1/scan/{pair}` | GET | Analyze a pair (e.g., `SPY_QQQ`) |
| `/api/v1/scan` | POST | Scan multiple pairs |
| `/api/v1/execute` | POST | Execute a trade |
| `/api/v1/positions` | GET | Active positions |
| `/api/v1/history` | GET | Order history |
| `/api/v1/theory` | GET | Mathematical documentation |
| `/docs` | GET | Interactive Swagger UI |

---

## 🔍 Scan Trading Opportunities

### Single Pair
```bash
curl http://localhost:8000/api/v1/scan/SPY_QQQ
```

### Multiple Pairs
```bash
curl -X POST http://localhost:8000/api/v1/scan \
  -H "Content-Type: application/json" \
  -d '{
    "pairs": [
      {"base": "SPY", "quote": "QQQ"},
      {"base": "GLD", "quote": "SLV"},
      {"base": "XOM", "quote": "CVX"}
    ],
    "mcts_iterations": 800
  }'
```

**Response includes:**
- Kalman Z-score
- EGARCH volatility regime
- MCTS expected value
- Recommended action (LONG_SPREAD / SHORT_SPREAD / HOLD)
- Confidence level

---

## 🛑 Stop the Bot

```bash
# Press Ctrl+C in the terminal

# Or kill from another terminal
pkill -f "python main.py"
```

---

## ❓ Troubleshooting

### "API keys not set"
```bash
# Check .env file exists and has correct keys
cat .env

# Restart the bot
./start-alpaca.sh
```

### "Connection refused"
```bash
# Make sure server is running
curl http://localhost:8000/health

# Check if port 8000 is in use
lsof -i :8000
```

### "Alpaca API connection failed"
```bash
# Verify keys are correct (no extra spaces)
# Check you're using PAPER keys, not live keys
# Ensure APCA_API_BASE_URL is set correctly
```

### Port already in use
```bash
# Kill existing process
pkill -f "python main.py"

# Or use a different port
export API_PORT=8001
python main.py
```

---

## 📖 Next Steps

1. **Start with dry-run** — Always test trades in simulation first
2. **Monitor signals** — Use the dashboard to scan pairs
3. **Small positions** — Start with small quantities (10 shares)
4. **Track performance** — Check `/api/v1/history` regularly
5. **Learn the math** — Read `/api/v1/theory` for strategy details

---

## 🆘 Support

- **Documentation:** `http://localhost:8000/docs`
- **Theory:** `http://localhost:8000/api/v1/theory`
- **Alpaca Docs:** https://alpaca.markets/docs

---

## ⚠️ Important Warnings

1. **Paper trading is NOT live trading** — Results may differ with real money
2. **Never share your API keys** — Keep `.env` file secure
3. **Start small** — Test with minimal quantities first
4. **Monitor positions** — Don't leave trades unattended
5. **Understand the strategy** — Read the mathematical foundations before trading

---

**Ready to trade?** Run `./start-alpaca.sh` and let's go! 🚀
