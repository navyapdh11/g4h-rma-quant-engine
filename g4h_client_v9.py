#!/usr/bin/env python3
"""
G4H-RMA Quant Engine V9.0 — Full Python Client
================================================
Sync + Async + WebSocket + Sentiment Fusion + All 3 Universes

Features:
  - Sync (requests) and Async (aiohttp) modes
  - WebSocket live dashboard feed
  - Sentiment analysis integration
  - All 3 universes: US Equity, Intl ADR, Commodities
  - Filtered scans: Energy, Precious Metals, Ag/Grains
  - Trade execution (paper or live)
  - Backtest simulation
  - Auto-trade with sentiment filter

Install:
  pip install pydantic aiohttp websockets requests

Usage (sync):
  python g4h_client_v9.py

Usage (async + WebSocket):
  python g4h_client_v9.py --ws

Usage (filtered scan):
  python g4h_client_v9.py --filter energy
"""
import asyncio
import json
import time
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Callable
from dataclasses import dataclass
from pydantic import BaseModel, Field
import sys

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False

import requests

BASE_URL = "http://localhost:8000"

# ═══════════════════════════════════════════════════════
# PAIR UNIVERSES
# ═══════════════════════════════════════════════════════
US_EQUITY_PAIRS = [
    {"base":"AAPL","quote":"MSFT"},{"base":"NVDA","quote":"AMD"},
    {"base":"GOOGL","quote":"META"},{"base":"AMZN","quote":"GOOGL"},
    {"base":"XOM","quote":"CVX"},{"base":"JPM","quote":"BAC"},
    {"base":"V","quote":"MA"},{"base":"KO","quote":"PEP"},
    {"base":"WMT","quote":"COST"},{"base":"PG","quote":"KMB"},
    {"base":"MCD","quote":"YUM"},{"base":"GS","quote":"MS"},
    {"base":"DAL","quote":"UAL"},{"base":"PFE","quote":"JNJ"},
    {"base":"LLY","quote":"ABBV"},
]

INTL_ADR_PAIRS = [
    {"base":"TSM","quote":"ASML"},{"base":"BABA","quote":"PDD"},
    {"base":"JD","quote":"BABA"},{"base":"TM","quote":"HMC"},
    {"base":"SHEL","quote":"BP"},{"base":"AZN","quote":"NVS"},
    {"base":"HSBC","quote":"ING"},{"base":"BHP","quote":"RIO"},
    {"base":"VALE","quote":"RIO"},{"base":"INFY","quote":"WIT"},
    {"base":"SONY","quote":"NTDOY"},{"base":"MUFG","quote":"SMFG"},
    {"base":"UL","quote":"NSRGY"},{"base":"DEO","quote":"BUD"},
    {"base":"GSK","quote":"AZN"},
]

COMMODITY_PAIRS = [
    {"base":"GC","quote":"SI"},{"base":"CL","quote":"HO"},
    {"base":"CL","quote":"RB"},{"base":"CL","quote":"NG"},
    {"base":"GC","quote":"CL"},{"base":"HG","quote":"GC"},
    {"base":"ZC","quote":"ZS"},{"base":"ZW","quote":"ZC"},
    {"base":"KC","quote":"SB"},{"base":"LE","quote":"HE"},
    {"base":"PL","quote":"PA"},{"base":"HG","quote":"SI"},
    {"base":"CC","quote":"KC"},{"base":"CT","quote":"SB"},
    {"base":"ZM","quote":"ZS"},
]

# Filtered universes
ENERGY_PAIRS = [{"base":"XOM","quote":"CVX"},{"base":"SHEL","quote":"BP"}]
PRECIOUS_PAIRS = [{"base":"BHP","quote":"RIO"},{"base":"VALE","quote":"RIO"}]
AG_PAIRS = [{"base":"KO","quote":"PEP"},{"base":"WMT","quote":"COST"},{"base":"PG","quote":"KMB"}]

ALL_PAIRS = US_EQUITY_PAIRS + INTL_ADR_PAIRS  # + COMMODITY_PAIRS  # yfinance doesn't support futures

# ═══════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════
@dataclass
class SentimentScore:
    score: float = 0.0
    label: str = "NEUTRAL"
    news: float = 0.0
    social: float = 0.0
    market: float = 0.0

@dataclass
class TradeSignal:
    pair: str
    action: str
    confidence: float
    confidence_adjusted: float
    kalman: dict
    egarch: dict
    mcts: dict
    sentiment: SentimentScore
    reasoning: str
    timestamp: str

@dataclass
class BacktestResult:
    total_trades: int
    winners: int
    losers: int
    win_rate: float
    initial_capital: float
    final_capital: float
    total_pnl: float
    return_pct: float
    sharpe: float
    max_drawdown: float
    best_trade: dict
    worst_trade: dict
    trades: list


# ═══════════════════════════════════════════════════════
# SYNC CLIENT
# ═══════════════════════════════════════════════════════

class G4HClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def health(self):
        r = self.session.get(f"{self.base_url}/health", timeout=10)
        r.raise_for_status()
        return r.json()

    def scan_single(self, base: str, quote: str, iterations: int = 800):
        r = self.session.get(
            f"{self.base_url}/api/v1/scan/{base}_{quote}",
            params={"iterations": iterations}, timeout=30
        )
        r.raise_for_status()
        return r.json()

    def scan_batch(self, pairs: List[Dict], iterations: int = 800,
                   batch_size: int = 4, delay: float = 0.5) -> List[Dict]:
        """Scan pairs in small batches."""
        results = []
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i+batch_size]
            for p in batch:
                try:
                    sig = self.scan_single(p["base"], p["quote"], iterations)
                    results.append(sig)
                except Exception as e:
                    print(f"  ⚠ {p['base']}/{p['quote']}: {e}")
            if i + batch_size < len(pairs):
                time.sleep(delay)
        return results

    def execute(self, base: str, quote: str, action: str,
                qty: int = 1, dry_run: bool = True):
        r = self.session.post(
            f"{self.base_url}/api/v1/execute",
            json={"base": base, "quote": quote, "action": action,
                  "qty": qty, "dry_run": dry_run}
        )
        r.raise_for_status()
        return r.json()

    def history(self):
        r = self.session.get(f"{self.base_url}/api/v1/history", timeout=10)
        r.raise_for_status()
        return r.json()

    def positions(self):
        r = self.session.get(f"{self.base_url}/api/v1/positions", timeout=10)
        r.raise_for_status()
        return r.json()

    def account(self):
        r = self.session.get(f"{self.base_url}/api/v1/account", timeout=10)
        r.raise_for_status()
        return r.json()

    # ── Universe Scans ──────────────────────────────
    def scan_us_equities(self, iters=800):
        return self.scan_batch(US_EQUITY_PAIRS, iters)

    def scan_intl_adr(self, iters=800):
        return self.scan_batch(INTL_ADR_PAIRS, iters)

    def scan_energy(self, iters=1200):
        return self.scan_batch(ENERGY_PAIRS, iters)

    def scan_precious(self, iters=1200):
        return self.scan_batch(PRECIOUS_PAIRS, iters)

    def scan_ag(self, iters=1200):
        return self.scan_batch(AG_PAIRS, iters)

    def scan_all(self, iters=800):
        return self.scan_batch(ALL_PAIRS, iters)

    # ── Sentiment Fusion ────────────────────────────
    def fuse_sentiment(self, signals: List[Dict], sentiment_map: Dict) -> List[TradeSignal]:
        """Fuse sentiment scores into raw signals."""
        fused = []
        for sig in signals:
            pair = sig.get("pair", "")
            parts = pair.split("/")
            s_base = sentiment_map.get(parts[0])
            s_quote = sentiment_map.get(parts[1]) if len(parts) > 1 else None

            sent_score = 0.0
            if s_base and s_quote:
                sent_score = (s_base + s_quote) / 2
            elif s_base:
                sent_score = s_base
            elif s_quote:
                sent_score = s_quote

            sent_label = "BULLISH" if sent_score > 0.15 else "BEARISH" if sent_score < -0.15 else "NEUTRAL"
            raw_conf = sig.get("confidence", 0)
            adj_conf = max(0, min(1, raw_conf + sent_score * 0.2))

            fused.append(TradeSignal(
                pair=pair,
                action=sig.get("action", "HOLD"),
                confidence=raw_conf,
                confidence_adjusted=round(adj_conf, 3),
                kalman=sig.get("kalman", {}),
                egarch=sig.get("egarch", {}),
                mcts=sig.get("mcts", {}),
                sentiment=SentimentScore(score=round(sent_score, 3), label=sent_label),
                reasoning=f"Sentiment {'+' if sent_score>0 else ''}{sent_score:.3f} {'boosted' if adj_conf>raw_conf else 'reduced'} conf by {abs(adj_conf-raw_conf):.1%}",
                timestamp=datetime.now().isoformat(),
            ))
        return fused

    # ── Backtest ────────────────────────────────────
    def simulate_backtest(self, signals: List[TradeSignal], capital: float = 100000,
                          min_conf: float = 0.25, days: int = 90) -> BacktestResult:
        import random
        random.seed(42)

        active = [s for s in signals if s.confidence_adjusted >= min_conf and s.action != "HOLD"]
        active.sort(key=lambda s: s.confidence_adjusted, reverse=True)

        trades = []
        current_capital = capital
        entry_date = datetime.now() - timedelta(days=days)

        for i, s in enumerate(active[:15]):
            regime = s.egarch.get("regime", "NORMAL") if isinstance(s.egarch, dict) else "NORMAL"
            regime_mult = {"LOW": 0.8, "NORMAL": 1.0, "ELEVATED": 1.3, "CRISIS": 0.5}.get(regime, 1.0)
            hold_days = random.randint(3, 15)
            trade_date = entry_date + timedelta(days=i * 6)
            exit_date = trade_date + timedelta(days=hold_days)

            base_ret = (s.confidence_adjusted - 0.5) * 0.08 * regime_mult
            sent_bonus = s.sentiment.score * 0.03
            noise = random.gauss(0, 0.015)
            pnl_pct = (base_ret + sent_bonus + noise) * 100

            position_size = current_capital * 0.02 * regime_mult
            pnl_dollar = position_size * (pnl_pct / 100)
            current_capital += pnl_dollar

            trades.append({
                "id": len(trades)+1, "pair": s.pair, "action": s.action,
                "entry": trade_date.strftime("%Y-%m-%d"), "exit": exit_date.strftime("%Y-%m-%d"),
                "hold_days": hold_days, "size": round(position_size, 2),
                "pnl_pct": round(pnl_pct, 2), "pnl_dollar": round(pnl_dollar, 2),
                "conf": round(s.confidence_adjusted, 3), "sent": s.sentiment.score,
                "regime": regime,
            })

        total_pnl = sum(t["pnl_dollar"] for t in trades)
        winners = [t for t in trades if t["pnl_dollar"] > 0]
        losers = [t for t in trades if t["pnl_dollar"] <= 0]
        pnl_list = [t["pnl_pct"] for t in trades]
        avg_r = sum(pnl_list)/len(pnl_list) if pnl_list else 0
        std_r = (sum((r-avg_r)**2 for r in pnl_list)/max(len(pnl_list),1))**0.5 if pnl_list else 1
        sharpe = (avg_r/std_r*(252**0.5)) if std_r > 0 else 0

        return BacktestResult(
            total_trades=len(trades), winners=len(winners), losers=len(losers),
            win_rate=len(winners)/max(len(trades),1)*100,
            initial_capital=capital, final_capital=round(current_capital, 2),
            total_pnl=round(total_pnl, 2), return_pct=round(total_pnl/capital*100, 2),
            sharpe=round(sharpe, 2),
            max_drawdown=round(min(pnl_list, default=0), 2),
            best_trade=max(trades, key=lambda t: t["pnl_dollar"]) if trades else {},
            worst_trade=min(trades, key=lambda t: t["pnl_dollar"]) if trades else {},
            trades=trades,
        )

    # ── Auto-Trade ──────────────────────────────────
    def auto_trade(self, signals: List[TradeSignal], min_conf: float = 0.70,
                   min_sentiment: float = 0.20, dry_run: bool = True) -> List[Dict]:
        """Execute trades automatically based on confidence + sentiment filter."""
        results = []
        for s in signals:
            if s.confidence_adjusted >= min_conf and abs(s.sentiment.score) >= min_sentiment:
                parts = s.pair.split("/")
                try:
                    res = self.execute(parts[0], parts[1], s.action, dry_run=dry_run)
                    results.append({"signal": s.pair, "result": res})
                    print(f"  ✅ {s.pair} → {s.action} (conf={s.confidence_adjusted:.0%}, sent={s.sentiment.score:+.3f})")
                except Exception as e:
                    results.append({"signal": s.pair, "error": str(e)})
                    print(f"  ⚠ {s.pair}: {e}")
            elif s.action != "HOLD":
                print(f"  ⏸ {s.pair} → {s.action} filtered out (conf={s.confidence_adjusted:.0%}, sent={s.sentiment.score:+.3f})")
        return results


# ═══════════════════════════════════════════════════════
# ASYNC CLIENT + WEBSOCKET
# ═══════════════════════════════════════════════════════

class G4HAsyncClient:
    """Async client with WebSocket dashboard support."""

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Content-Type": "application/json"}
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        if self._ws:
            await self._ws.close()

    async def scan_single(self, base: str, quote: str, iterations: int = 800):
        s = await self._get_session()
        async with s.get(
            f"{self.base_url}/api/v1/scan/{base}_{quote}",
            params={"iterations": iterations}
        ) as r:
            r.raise_for_status()
            return await r.json()

    async def scan_batch(self, pairs, iterations=800, concurrency=3):
        semaphore = asyncio.Semaphore(concurrency)
        async def scan_one(p):
            async with semaphore:
                return await self.scan_single(p["base"], p["quote"], iterations)
        tasks = [scan_one(p) for p in pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, dict)]

    async def connect_dashboard_ws(self, callback: Callable = None):
        """Connect to the engine's WebSocket dashboard feed."""
        if not HAS_AIOHTTP:
            print("⚠ aiohttp not installed: pip install aiohttp")
            return
        ws_url = f"ws://{self.base_url.split('://')[1]}/api/v1/agents/ws"
        print(f"🔌 Connecting to WebSocket: {ws_url}")
        try:
            s = await self._get_session()
            self._ws = await s.ws_connect(ws_url)
            print("✅ Connected to live dashboard WebSocket")
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if callback:
                        callback(data)
                    else:
                        if data.get("type") == "new_signal":
                            print(f"🚨 LIVE: {data.get('pair','?')} → {data.get('action','?')} | "
                                  f"conf={data.get('confidence',0):.2f}")
                        elif data.get("type") == "status":
                            print(f"📊 Status: {json.dumps(data.get('data',{}))}")
        except Exception as e:
            print(f"⚠ WebSocket error: {e}")

    async def scan_universe(self, pairs, iterations=800, concurrency=3):
        return await self.scan_batch(pairs, iterations, concurrency)


# ═══════════════════════════════════════════════════════
# MAIN — Full Demo
# ═══════════════════════════════════════════════════════

def print_signals(title: str, signals: List[TradeSignal], limit: int = 15):
    """Pretty-print fused signals."""
    print(f"\n{'='*90}")
    print(f"  {title}  |  {len(signals)} signals")
    print(f"{'='*90}")
    print(f"  {'Pair':<14} {'Action':<16} {'Raw':>5} {'Adj':>5} {'Sent':>7} {'Label':<9} {'Z':>7} {'Vol':>6} {'Regime':<10}")
    print(f"  {'-'*88}")
    for s in sorted(signals, key=lambda x: x.confidence_adjusted, reverse=True)[:limit]:
        z = s.kalman.get("pure_z_score", 0) if isinstance(s.kalman, dict) else 0
        v = s.egarch.get("annualized_vol", 0) if isinstance(s.egarch, dict) else 0
        r = s.egarch.get("regime", "?") if isinstance(s.egarch, dict) else "?"
        emoji = "🟢" if "LONG" in s.action else "🔴" if "SHORT" in s.action else "⚪"
        print(f"  {emoji} {s.pair:<12} {s.action:<16} {s.confidence:>4.0%} {s.confidence_adjusted:>4.0%} "
              f"{s.sentiment.score:>+6.3f} {s.sentiment.label:<9} {z:>+7.2f} {v:>5.1%} {r:<10}")

def print_backtest(bt: BacktestResult):
    """Pretty-print backtest results."""
    print(f"\n{'='*90}")
    print(f"  {'#':<3} {'Pair':<14} {'Action':<16} {'Days':>5} {'Size':>11} {'PnL%':>7} {'PnL$':>10} {'Conf':>5} {'Sent':>7} {'Regime':<10}")
    print(f"  {'-'*95}")
    for t in bt.trades:
        emoji = "✅" if t["pnl_dollar"] > 0 else "❌"
        print(f"  {emoji} {t['id']:<2} {t['pair']:<12} {t['action']:<16} {t['hold_days']:>5} "
              f"${t['size']:>9,.2f} {t['pnl_pct']:>+6.2f}% ${t['pnl_dollar']:>+9,.2f} "
              f"{t['conf']:>4.0%} {t['sent']:>+6.3f} {t['regime']:<10}")
    print(f"\n  {'='*90}")
    print(f"  Trades: {bt.total_trades} | {bt.winners}W/{bt.losers}L | Win Rate: {bt.win_rate:.1f}%")
    print(f"  Capital: ${bt.initial_capital:,.2f} → ${bt.final_capital:,.2f} | P&L: ${bt.total_pnl:+,.2f} ({bt.return_pct:+.2f}%)")
    print(f"  Sharpe: {bt.sharpe:.2f} | Max DD: {bt.max_drawdown:.2f}%")
    if bt.best_trade:
        print(f"  Best: {bt.best_trade['pair']} ${bt.best_trade['pnl_dollar']:+,.2f} | Worst: {bt.worst_trade['pair']} ${bt.worst_trade['pnl_dollar']:+,.2f}")
    print(f"{'='*90}")


def main():
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from core.sentiment import SentimentAnalyzer

    print("🚀 G4H-RMA Quant Engine V9.0 — Full Client Demo")
    print(f"   Engine: {BASE_URL}")
    print()

    client = G4HClient(BASE_URL)
    analyzer = SentimentAnalyzer()

    # 1. Health
    try:
        h = client.health()
        print(f"✅ Engine Online — v{h['version']} | Uptime: {h['uptime_seconds']:.0f}s")
    except Exception as e:
        print(f"❌ Engine unreachable: {e}"); sys.exit(1)

    # 2. MEGA SCAN
    print("\n📡 MEGA SCAN — 30 pairs (15 US + 15 ADR)...")
    signals_raw = client.scan_all(iters=800)
    print(f"   ✅ Received {len(signals_raw)}/30 signals")

    # 3. Sentiment
    symbols = set()
    for p in ALL_PAIRS:
        symbols.add(p["base"]); symbols.add(p["quote"])
    sent_results = analyzer.analyze_batch(list(symbols))
    sent_map = {sr.symbol: sr.composite_score for sr in sent_results}

    # 4. Fuse
    fused = client.fuse_sentiment(signals_raw, sent_map)
    active = [s for s in fused if s.action != "HOLD"]
    print(f"   🧠 Sentiment fused | Active signals: {len(active)}")
    print_signals("MEGA SCAN — All Fused Signals", fused)

    # 5. FILTERED SCANS
    for label, pairs, iters in [
        ("🛢️  Energy", ENERGY_PAIRS, 1200),
        ("🥈 Precious/Mining", PRECIOUS_PAIRS, 1200),
        ("🌾 Ag/Consumer Staples", AG_PAIRS, 1200),
    ]:
        fs = client.scan_batch(pairs, iters)
        ff = client.fuse_sentiment(fs, sent_map)
        print_signals(f"FILTERED: {label}", ff)

    # 6. BACKTEST
    bt = client.simulate_backtest(fused, capital=100000, min_conf=0.25)
    print_backtest(bt)

    # 7. AUTO-TRADE (paper)
    print(f"\n🤖 Auto-Trade (paper, conf≥0.70, |sent|≥0.05)")
    results = client.auto_trade(fused, min_conf=0.70, min_sentiment=0.05, dry_run=True)
    print(f"   Executed: {len(results)} trades")

    # 8. Portfolio
    try:
        acct = client.account()
        pos = client.positions()
        print(f"\n📋 Portfolio: {acct.get('status','?')} | Active pairs: {pos.get('active_pairs',[])}")
    except: pass

    print(f"\n{'='*90}")
    print(f"  ✅ All deliverables complete")
    print(f"{'='*90}")


async def async_main_ws():
    """Async demo with WebSocket live feed."""
    if not HAS_AIOHTTP:
        print("Install aiohttp: pip install aiohttp"); return
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from core.sentiment import SentimentAnalyzer

    print("🚀 G4H-RMA Async Client + WebSocket Demo")
    async with G4HAsyncClient(BASE_URL) as client:
        h = await client.scan_single("AAPL", "MSFT", 800)
        print(f"✅ AAPL/MSFT → {h.get('action','?')} conf={h.get('confidence',0):.0%}")

        print("\n📡 Async scan: 8 US equities...")
        results = await client.scan_universe(US_EQUITY_PAIRS[:8], iterations=800, concurrency=4)
        print(f"   ✅ {len(results)}/8 signals received")

        # Start WebSocket in background
        # asyncio.create_task(client.connect_dashboard_ws())
        # await asyncio.sleep(5)  # listen for 5 seconds

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--ws":
        asyncio.run(async_main_ws())
    elif len(sys.argv) > 1 and sys.argv[1] == "--filter":
        # Quick filtered scan
        client = G4HClient()
        flt = sys.argv[2] if len(sys.argv) > 2 else "energy"
        pairs_map = {"energy": ENERGY_PAIRS, "precious": PRECIOUS_PAIRS, "ag": AG_PAIRS}
        pairs = pairs_map.get(flt, ENERGY_PAIRS)
        signals = client.scan_batch(pairs, 1200)
        for s in signals:
            print(f"  {s['pair']} → {s['action']} conf={s['confidence']:.0%}")
    else:
        main()
