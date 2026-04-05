#!/usr/bin/env python3
"""
G4H-RMA Quant Engine V8.0 — Institutional Edition
==================================================
Usage:
  python main.py              Start API server
  python main.py --scan       CLI pair scanner
  python main.py --backtest   CLI backtest
  python main.py --theory     Math documentation
  python main.py --validate   Validate configuration

V8.0 Enhancements:
  - Parallel MCTS with multi-threaded search
  - Advanced risk: VaR, CVaR, portfolio optimization
  - WebSocket streaming for real-time market data
  - Enhanced AI agent reasoning with conflict resolution
  - Comprehensive performance metrics dashboard
  - Dynamic position sizing based on Kelly criterion
  - Multi-timeframe analysis support
  - Improved error recovery and self-healing
"""
from __future__ import annotations
import sys
import asyncio
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)


def main():
    args = sys.argv[1:]
    
    if "--validate" in args:
        _validate_config()
        return
    
    if "--theory" in args:
        _print_theory()
        return
    
    if "--scan" in args:
        asyncio.run(_cli_scan())
        return
    
    if "--backtest" in args:
        _cli_backtest()
        return
    
    # Default: start API server
    import uvicorn
    from config import settings

    # Validate configuration before starting
    errors = settings.validate_all()
    if errors:
        logging.error("Configuration validation failed:")
        for error in errors:
            logging.error(f"  - {error}")
        sys.exit(1)

    print("=" * 60)
    print("  G4H-RMA Quant Engine V8.0 — Institutional Edition")
    print("  Kalman + EGARCH + MCTS + Alpaca + CCXT + SQLite")
    print("=" * 60)
    print(f"  API:  http://{settings.api.host}:{settings.api.port}")
    print(f"  Docs: http://{settings.api.host}:{settings.api.port}/docs")
    print(f"  Theory: http://{settings.api.host}:{settings.api.port}/api/v1/theory")
    print("=" * 60)

    uvicorn.run(
        "api.app:app",
        host=settings.api.host,
        port=settings.api.port,
        workers=settings.api.workers,
        log_level="info",
    )


def _validate_config():
    """Validate configuration."""
    from config import settings
    
    print("\n" + "=" * 60)
    print("  Configuration Validation")
    print("=" * 60)
    
    errors = settings.validate_all()
    
    if errors:
        print("\n❌ VALIDATION FAILED\n")
        for error in errors:
            print(f"  • {error}")
        sys.exit(1)
    else:
        print("\n✅ ALL CHECKS PASSED\n")
        print(f"  Kalman: warmup={settings.kalman.warmup_steps} steps")
        print(f"  EGARCH: lookback={settings.egarch.lookback_days} days")
        print(f"  MCTS: iterations={settings.mcts.iterations}")
        print(f"  Risk: max_daily_trades={settings.risk.max_daily_trades}")
        print(f"  API: port={settings.api.port}")
        print()


async def _cli_scan():
    """CLI pair scanner."""
    import pandas as pd
    from config import settings
    from core.kalman import MultivariateKalmanFilter
    from core.egarch import EGARCHVolatilityModel
    from core.mcts import MCTSEngine
    from data.fetcher import DataFetcher
    
    fetcher = DataFetcher(settings.data)
    egarch = EGARCHVolatilityModel(settings.egarch)
    mcts = MCTSEngine(settings.mcts)
    pairs = settings.universe.equity_pairs + settings.universe.crypto_pairs_yf
    
    print(f"\n{'=' * 78}")
    print(f"  Scanning {len(pairs)} pairs...")
    print(f"{'=' * 78}")
    print(f"{'Pair':<16} {'Z-Score':>8} {'Action':>14} {'MCTS EV':>8} {'Vol':>7} {'Regime':>9}")
    print(f"{'-' * 78}")
    
    for base, quote in pairs:
        try:
            df = await fetcher.get_yfinance(base, quote, settings.data.default_period)
            if df is None or len(df) < 60:
                print(f"{base}/{quote:<12} {'NO DATA':>8} {'NO DATA':>14}")
                continue
            
            pa, pb = df[base], df[quote]
            kf = MultivariateKalmanFilter(settings.kalman)
            snap = None
            for i in range(len(pa)):
                snap = kf.step(float(pa.iloc[i]), float(pb.iloc[i]))
            
            if not snap or snap.is_divergent:
                print(f"{base}/{quote:<12} {'DIVERGED':>8}")
                continue
            
            ev = egarch.analyze(pa, cache_key=base)
            vs = egarch.get_vol_scale(ev.regime)
            mr = mcts.search(snap.spread, snap.innovation_var, vs)
            
            print(f"{base}/{quote:<12} {snap.pure_z_score:>+8.2f} "
                  f"{mr.action.value:>14} {mr.expected_value:>+8.3f} "
                  f"{ev.annualized_vol:>6.1%} {ev.regime.value:>9}")
                  
        except Exception as e:
            print(f"{base}/{quote:<12} ERROR: {e}")
    
    print(f"{'=' * 78}\n")


def _cli_backtest():
    """CLI backtest."""
    from backtest import run_backtest
    from models import BacktestRequest

    print("\n" + "=" * 60)
    print("  Backtest: SPY/QQQ (2020 -> today)")
    print("=" * 60)

    r = run_backtest(BacktestRequest(base="SPY", quote="QQQ"))
    m = r.metrics

    print(f"\n  Return:        {m.total_return:+.2%}")
    print(f"  Sharpe:        {m.sharpe_ratio:.2f}")
    print(f"  Sortino:       {m.sortino_ratio:.2f}" if m.sortino_ratio else "")
    print(f"  Max DD:        {m.max_drawdown:.2%}")
    print(f"  Win Rate:      {m.win_rate:.1%}")
    print(f"  Total Trades:  {m.total_trades}")
    print(f"  Avg PnL:       {m.avg_trade_pnl:+.4f}")
    print(f"  Profit Factor: {m.profit_factor:.2f}" if m.profit_factor != float('inf') else "  Profit Factor: ∞ (no losses)")

    if m.max_consecutive_wins:
        print(f"  Max Consec W:  {m.max_consecutive_wins}")
    if m.max_consecutive_losses:
        print(f"  Max Consec L:  {m.max_consecutive_losses}")

    if r.warnings:
        print(f"\n  Warnings:")
        for w in r.warnings:
            print(f"    • {w}")

    print()


def _print_theory():
    """Print mathematical foundations."""
    print("""
================================================================
  G4H-RMA QUANT ENGINE V7.0 — MATHEMATICAL FOUNDATIONS
================================================================

1. MULTIVARIATE KALMAN FILTER
   State:      x = [beta, alpha]^T
   Transition: x_k = F*x_{k-1} + w   (F=I, w~N(0,Q))
   Observation: z_k = H_k*x_k + v     (z=Price_A, H=[Price_B, 1])

   Predict:
     x^- = x,    P^- = P + Q

   Innovation:
     y = z - H*x^-,    S = H*P^-*H' + R

   Kalman Gain:
     K = P^-*H' * S^-1

   Update (Joseph Form — symmetry-preserving):
     x^+ = x^- + K*y
     P^+ = (I - K*H)*P^-*(I - K*H)' + K*R*K'

   Pure Z-Score (no look-ahead bias):
     Z = y / sqrt(S)

2. EGARCH(1,1)
   log(sigma^2_t) = w + b*log(sigma^2_{t-1}) + a*|z_{t-1}| + g*z_{t-1}
   g < 0 => leverage effect (negative shocks raise vol)

3. MCTS (Monte Carlo Tree Search)
   Selection: UCB1(n) = Q(n)/N(n) + c*sqrt(ln(N_parent)/N(n))
   Simulation: dz = lambda*(mu - z)*dt + sigma*sqrt(dt)*N(0,1)
   Policy: Most-visited child (robust selection)

4. GRAPH OF THOUGHTS
   Kalman -> [spread, S] -> EGARCH -> vol_scale -> MCTS -> EV -> Risk -> Execute

================================================================
""")


if __name__ == "__main__":
    main()
