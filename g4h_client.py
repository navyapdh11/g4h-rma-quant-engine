#!/usr/bin/env python3
"""
G4H-RMA Quant Engine V8.0 — Full Python Client
================================================
Scan & execute trades across US Equities, International ADRs, and Commodity Futures.

Supports:
  - Sync (requests) and Async (aiohttp) modes
  - All 45 pairs (15 US + 15 Intl ADR + 15 Commodities)
  - Single-pair drill-down with configurable MCTS iterations
  - Trade execution (paper or live)
  - Health check, history, positions, account queries

Install:
  pip install pydantic aiohttp requests

Usage (sync):
  python g4h_client.py

Usage (async):
  python -c "import asyncio; from g4h_client import async_main; asyncio.run(async_main())"
"""
import asyncio
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import json
import sys

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

import requests

BASE_URL = "http://localhost:8000"

# ═══════════════════════════════════════════════════════════
# PAIR UNIVERSES
# ═══════════════════════════════════════════════════════════

US_EQUITY_PAIRS = [
    {"base": "AAPL", "quote": "MSFT"},  {"base": "NVDA", "quote": "AMD"},
    {"base": "GOOGL", "quote": "META"}, {"base": "AMZN", "quote": "GOOGL"},
    {"base": "XOM", "quote": "CVX"},    {"base": "JPM", "quote": "BAC"},
    {"base": "V", "quote": "MA"},       {"base": "KO", "quote": "PEP"},
    {"base": "WMT", "quote": "COST"},   {"base": "PG", "quote": "KMB"},
    {"base": "MCD", "quote": "YUM"},    {"base": "GS", "quote": "MS"},
    {"base": "DAL", "quote": "UAL"},    {"base": "PFE", "quote": "JNJ"},
    {"base": "LLY", "quote": "ABBV"},
]

INTL_ADR_PAIRS = [
    {"base": "TSM", "quote": "ASML"},  {"base": "BABA", "quote": "PDD"},
    {"base": "JD", "quote": "BABA"},   {"base": "TM", "quote": "HMC"},
    {"base": "SHEL", "quote": "BP"},   {"base": "AZN", "quote": "NVS"},
    {"base": "HSBC", "quote": "ING"},  {"base": "BHP", "quote": "RIO"},
    {"base": "VALE", "quote": "RIO"},  {"base": "INFY", "quote": "WIT"},
    {"base": "SONY", "quote": "NTDOY"},{"base": "MUFG", "quote": "SMFG"},
    {"base": "UL", "quote": "NSRGY"},  {"base": "DEO", "quote": "BUD"},
    {"base": "GSK", "quote": "AZN"},
]

COMMODITY_PAIRS = [
    {"base": "GC", "quote": "SI"},  {"base": "CL", "quote": "HO"},
    {"base": "CL", "quote": "RB"},  {"base": "CL", "quote": "NG"},
    {"base": "GC", "quote": "CL"},  {"base": "HG", "quote": "GC"},
    {"base": "ZC", "quote": "ZS"},  {"base": "ZW", "quote": "ZC"},
    {"base": "KC", "quote": "SB"},  {"base": "LE", "quote": "HE"},
    {"base": "PL", "quote": "PA"},  {"base": "HG", "quote": "SI"},
    {"base": "CC", "quote": "KC"},  {"base": "CT", "quote": "SB"},
    {"base": "ZM", "quote": "ZS"},
]

ALL_PAIRS = US_EQUITY_PAIRS + INTL_ADR_PAIRS + COMMODITY_PAIRS

# ═══════════════════════════════════════════════════════════
# PYDANTIC MODELS (auto-aligned with engine OpenAPI schema)
# ═══════════════════════════════════════════════════════════

class AssetPair(BaseModel):
    base: str
    quote: str

class ScanRequest(BaseModel):
    pairs: List[AssetPair]
    mcts_iterations: int = 1200
    source: str = "KALMAN_MCTS"

class KalmanState(BaseModel):
    beta: float = 0.0
    alpha: float = 0.0
    spread: float = 0.0
    innovation_variance: float = 0.0
    pure_z_score: float = 0.0
    converged: bool = True

class EGARCHResult(BaseModel):
    annualized_vol: float = 0.0
    forecast_vol: float = 0.0
    leverage_gamma: Optional[float] = None
    regime: str = "LOW"
    params: Optional[Dict[str, Any]] = None

class MCTSResult(BaseModel):
    action: str = "HOLD"
    expected_value: float = 0.0
    visit_distribution: Optional[Dict[str, Any]] = None
    avg_reward_distribution: Optional[Dict[str, Any]] = None

class TradeSignal(BaseModel):
    pair: str = ""
    action: str = "HOLD"
    confidence: float = 0.0
    kalman: Optional[KalmanState] = None
    egarch: Optional[EGARCHResult] = None
    mcts: Optional[MCTSResult] = None
    source: str = ""
    timestamp: str = ""
    reasoning: str = ""

class ScanResponse(BaseModel):
    scan_id: str = ""
    signals: List[TradeSignal] = []
    errors: List[str] = []
    summary: Optional[Dict[str, Any]] = None

class ExecutionResponse(BaseModel):
    status: str = ""
    action: str = ""
    pair: str = ""
    details: str = ""
    simulated: bool = True
    error: Optional[str] = None

class HealthResponse(BaseModel):
    status: str = ""
    version: str = ""
    uptime_seconds: float = 0.0
    modules: Dict[str, bool] = {}
    errors: List[str] = []

# ═══════════════════════════════════════════════════════════
# SYNC CLIENT
# ═══════════════════════════════════════════════════════════

class G4HClient:
    """Sync + optional Async client for G4H-RMA Quant Engine."""

    def __init__(self, base_url: str = BASE_URL, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    # ── Core ──
    def health(self) -> HealthResponse:
        r = self.session.get(f"{self.base_url}/health")
        r.raise_for_status()
        return HealthResponse.model_validate(r.json())

    def scan(self, request: ScanRequest) -> ScanResponse:
        """Batch scan multiple pairs."""
        r = self.session.post(
            f"{self.base_url}/api/v1/scan",
            json=request.model_dump()
        )
        r.raise_for_status()
        return ScanResponse.model_validate(r.json())

    def scan_single(self, base: str, quote: str, iterations: int = 800) -> TradeSignal:
        """Drill-down scan on a single pair."""
        r = self.session.get(
            f"{self.base_url}/api/v1/scan/{base}_{quote}",
            params={"iterations": iterations}
        )
        r.raise_for_status()
        return TradeSignal.model_validate(r.json())

    def execute(self, base: str, quote: str, action: str,
                qty: int = 1, dry_run: bool = True) -> ExecutionResponse:
        """Execute a trade signal."""
        r = self.session.post(
            f"{self.base_url}/api/v1/execute",
            json={"base": base, "quote": quote, "action": action,
                  "qty": qty, "dry_run": dry_run}
        )
        r.raise_for_status()
        return ExecutionResponse.model_validate(r.json())

    def history(self) -> List[Dict]:
        r = self.session.get(f"{self.base_url}/api/v1/history")
        r.raise_for_status()
        return r.json()

    def positions(self) -> Dict:
        r = self.session.get(f"{self.base_url}/api/v1/positions")
        r.raise_for_status()
        return r.json()

    def account(self) -> Dict:
        r = self.session.get(f"{self.base_url}/api/v1/account")
        r.raise_for_status()
        return r.json()

    # ── Convenience ──
    def scan_universe(self, pairs: List[Dict], iterations: int = 1200,
                      batch_size: int = 5, delay: float = 0.5) -> List[TradeSignal]:
        """Scan a universe of pairs in batches (respects rate limits)."""
        import time
        all_signals = []
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i + batch_size]
            req = ScanRequest(
                pairs=[AssetPair(**p) for p in batch],
                mcts_iterations=iterations
            )
            try:
                resp = self.scan(req)
                all_signals.extend(resp.signals)
                if resp.errors:
                    for e in resp.errors:
                        print(f"  ⚠ {e}")
            except Exception as e:
                print(f"  ✗ Batch {i//batch_size + 1} failed: {e}")
            if i + batch_size < len(pairs):
                time.sleep(delay)
        return all_signals

    def scan_all(self, iterations: int = 800) -> List[TradeSignal]:
        """Scan all 45 pairs across all universes."""
        return self.scan_universe(ALL_PAIRS, iterations=iterations)

    def scan_us_equities(self, iterations: int = 1200) -> List[TradeSignal]:
        return self.scan_universe(US_EQUITY_PAIRS, iterations=iterations)

    def scan_intl_adr(self, iterations: int = 1200) -> List[TradeSignal]:
        return self.scan_universe(INTL_ADR_PAIRS, iterations=iterations)

    def scan_commodities(self, iterations: int = 1200) -> List[TradeSignal]:
        return self.scan_universe(COMMODITY_PAIRS, iterations=iterations)

    def top_signals(self, signals: List[TradeSignal], n: int = 5) -> List[TradeSignal]:
        """Return top N signals by confidence (excluding HOLD)."""
        active = [s for s in signals if s.action != "HOLD"]
        return sorted(active, key=lambda s: s.confidence, reverse=True)[:n]

    def print_signals(self, signals: List[TradeSignal], title: str = "Signals"):
        """Pretty-print signals to console."""
        print(f"\n{'='*70}")
        print(f"  {title}  |  {len(signals)} signals")
        print(f"{'='*70}")
        print(f"  {'Pair':<14} {'Action':<16} {'Conf':>6} {'Z-Score':>8} {'Vol':>7} {'Regime':<8}")
        print(f"  {'-'*66}")
        for s in signals:
            z = s.kalman.pure_z_score if s.kalman else 0
            v = s.egarch.annualized_vol if s.egarch else 0
            r = s.egarch.regime if s.egarch else "?"
            emoji = "🟢" if s.action in ("LONG_SPREAD", "BUY") else \
                    "🔴" if s.action in ("SHORT_SPREAD", "SELL") else "⚪"
            print(f"  {emoji} {s.pair:<12} {s.action:<16} {s.confidence:>5.1%} {z:>+8.2f} {v:>6.1%} {r:<8}")
        print(f"{'='*70}")


# ═══════════════════════════════════════════════════════════
# ASYNC CLIENT (aiohttp)
# ═══════════════════════════════════════════════════════════

class G4HAsyncClient:
    """Async client using aiohttp."""

    def __init__(self, base_url: str = BASE_URL, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def health(self) -> HealthResponse:
        s = await self._get_session()
        async with s.get(f"{self.base_url}/health") as r:
            r.raise_for_status()
            return HealthResponse.model_validate(await r.json())

    async def scan_single(self, base: str, quote: str, iterations: int = 800) -> TradeSignal:
        s = await self._get_session()
        async with s.get(
            f"{self.base_url}/api/v1/scan/{base}_{quote}",
            params={"iterations": iterations}
        ) as r:
            r.raise_for_status()
            return TradeSignal.model_validate(await r.json())

    async def scan(self, request: ScanRequest) -> ScanResponse:
        s = await self._get_session()
        async with s.post(
            f"{self.base_url}/api/v1/scan",
            json=request.model_dump()
        ) as r:
            r.raise_for_status()
            return ScanResponse.model_validate(await r.json())

    async def execute(self, base: str, quote: str, action: str,
                      qty: int = 1, dry_run: bool = True) -> ExecutionResponse:
        s = await self._get_session()
        async with s.post(
            f"{self.base_url}/api/v1/execute",
            json={"base": base, "quote": quote, "action": action,
                  "qty": qty, "dry_run": dry_run}
        ) as r:
            r.raise_for_status()
            return ExecutionResponse.model_validate(await r.json())

    async def scan_universe_async(self, pairs: List[Dict], iterations: int = 1200,
                                   concurrency: int = 3) -> List[TradeSignal]:
        """Scan universe with concurrent single-pair scans."""
        semaphore = asyncio.Semaphore(concurrency)
        async def scan_one(p):
            async with semaphore:
                return await self.scan_single(p["base"], p["quote"], iterations)
        tasks = [scan_one(p) for p in pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        signals = []
        for r in results:
            if isinstance(r, TradeSignal):
                signals.append(r)
            elif isinstance(r, Exception):
                print(f"  ⚠ Scan failed: {r}")
        return signals


# ═══════════════════════════════════════════════════════════
# MAIN — Demo: Scan all 3 universes
# ═══════════════════════════════════════════════════════════

def main():
    print("🚀 G4H-RMA Quant Engine V8.0 — Python Client Demo")
    print(f"   Engine: {BASE_URL}")

    client = G4HClient(BASE_URL)

    # 1. Health check
    try:
        health = client.health()
        print(f"\n✅ Engine Online — v{health.version} | Uptime: {health.uptime_seconds:.0f}s")
        mods = [k for k, v in health.modules.items() if v]
        print(f"   Modules: {', '.join(mods)}")
    except Exception as e:
        print(f"\n❌ Engine unreachable: {e}")
        sys.exit(1)

    # 2. Scan US Equities (top 8)
    print("\n📡 Scanning US Equities (Top 8)...")
    eq_signals = client.scan_us_equities(iterations=800)
    client.print_signals(
        client.top_signals(eq_signals, n=5),
        "US Equities — Top Signals"
    )

    # 3. Scan International ADRs (top 8)
    print("\n📡 Scanning International ADRs (Top 8)...")
    adr_signals = client.scan_intl_adr(iterations=800)
    client.print_signals(
        client.top_signals(adr_signals, n=5),
        "International ADR — Top Signals"
    )

    # 4. Scan Commodities (top 8)
    print("\n📡 Scanning Commodities (Top 8)...")
    comm_signals = client.scan_commodities(iterations=800)
    client.print_signals(
        client.top_signals(comm_signals, n=5),
        "Commodities — Top Signals"
    )

    # 5. Cross-universe summary
    all_active = [s for s in eq_signals + adr_signals + comm_signals if s.action != "HOLD"]
    all_sorted = sorted(all_active, key=lambda s: s.confidence, reverse=True)
    print(f"\n🏆 CROSS-UNIVERSE TOP 5 STRONGEST SIGNALS")
    client.print_signals(all_sorted[:5], "All 3 Universes Combined")

    # 6. Example: Execute top signal (paper trade)
    if all_sorted:
        top = all_sorted[0]
        print(f"\n💰 Executing strongest signal (paper): {top.pair} → {top.action}")
        try:
            result = client.execute(
                base=top.pair.split("/")[0],
                quote=top.pair.split("/")[1],
                action=top.action,
                qty=1,
                dry_run=True
            )
            print(f"   Status: {result.status} | {result.details}")
        except Exception as e:
            print(f"   ⚠ Execution skipped: {e}")

    # 7. Show portfolio snapshot
    try:
        acct = client.account()
        pos = client.positions()
        print(f"\n📋 Portfolio Snapshot:")
        print(f"   Account: {acct.get('status', '?')}")
        print(f"   Active pairs: {pos.get('active_pairs', [])}")
    except Exception as e:
        print(f"\n   ⚠ Account/positions: {e}")

    print(f"\n{'='*70}")
    print(f"  Done. Full API docs: {BASE_URL}/docs")
    print(f"{'='*70}")


async def async_main():
    """Async version of the demo."""
    if not HAS_AIOHTTP:
        print("aiohttp not installed. Run: pip install aiohttp")
        return
    print("🚀 G4H-RMA Async Client Demo")
    async with G4HAsyncClient(BASE_URL) as client:
        health = await client.health()
        print(f"✅ Engine Online — v{health.version}")

        print("\n📡 Scanning all 15 commodity pairs (async, concurrent=5)...")
        signals = await client.scan_universe_async(
            COMMODITY_PAIRS, iterations=800, concurrency=5
        )
        print(f"   Received {len(signals)}/15 signals")
        for s in signals[:5]:
            z = s.kalman.pure_z_score if s.kalman else 0
            print(f"   {s.pair} → {s.action} | conf={s.confidence:.2f} | z={z:+.2f}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--async":
        asyncio.run(async_main())
    else:
        main()
