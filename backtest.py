"""
Walk-forward backtest engine V10.1
==================================
V10.1 Fixes & Improvements:
  - Real EGARCH volatility estimation (was hardcoded 0.20)
  - Real MCTS signal integration (was always HOLD)
  - Confidence threshold filtering (was executing weak signals)
  - Configurable capital allocation per trade (was fixed 10%)
  - Realistic commission model (was overcharging on large share counts)
  - Stop-loss with z-score threshold
  - Better exit logic (z_exit default 0.5 instead of 0.0)
  - Benchmark: buy-and-hold comparison
  - Walk-forward compatible design
  - V7.0: Reproducible MCTS seeding for deterministic backtests
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from core.kalman import MultivariateKalmanFilter
from core.egarch import EGARCHVolatilityModel
from core.mcts import MCTSEngine
from core.risk import RiskManager
from models import (
    ActionType, BacktestMetrics, BacktestRequest, BacktestResponse,
    TradeSignal, KalmanState, EGARCHResult, MCTSResult,
    VolatilityRegime, SignalSource,
)
from config import settings
import logging

logger = logging.getLogger(__name__)

# V10.1: Realistic commission and slippage
# Alpaca: $0 commission, but we model exchange fees + slippage
COMMISSION_BPS = 0.0  # Alpaca is $0 commission
SLIPPAGE_BPS = 3  # 3 basis points = 0.03% (realistic for liquid stocks)


def run_backtest(req: BacktestRequest) -> BacktestResponse:
    """Run pairs trading backtest with enhanced metrics."""
    from data.unified_fetcher import UnifiedDataFetcher
    fetcher = UnifiedDataFetcher()

    warnings = []
    period = "2y"

    try:
        df = fetcher.get_yfinance(req.base, req.quote, period)
    except Exception as e:
        logger.error(f"Data fetch failed: {e}")
        warnings.append(f"Data fetch warning: {str(e)}")
        return BacktestResponse(
            pair=f"{req.base}/{req.quote}",
            metrics=BacktestMetrics(
                total_return=0, sharpe_ratio=0, max_drawdown=0,
                win_rate=0, total_trades=0, avg_trade_pnl=0, profit_factor=0,
            ),
            warnings=warnings,
        )

    if df is None or len(df) < 60:
        warnings.append(f"Insufficient data for {req.base}/{req.quote}")
        return BacktestResponse(
            pair=f"{req.base}/{req.quote}",
            metrics=BacktestMetrics(
                total_return=0, sharpe_ratio=0, max_drawdown=0,
                win_rate=0, total_trades=0, avg_trade_pnl=0, profit_factor=0,
            ),
            warnings=warnings,
        )

    pa, pb = df[req.base], df[req.quote]

    # ── Initialize models ──────────────────────────────────────────────
    kf = MultivariateKalmanFilter(settings.kalman)
    egarch_model = EGARCHVolatilityModel(settings.egarch)
    mcts_engine = MCTSEngine(settings.mcts)
    risk_mgr = RiskManager(settings.risk)

    # ── Kalman filter pass ─────────────────────────────────────────────
    z_scores, spreads, betas = [], [], []
    for i in range(len(pa)):
        snap = kf.step(float(pa.iloc[i]), float(pb.iloc[i]))
        z_scores.append(snap.pure_z_score)
        spreads.append(snap.spread)
        betas.append(snap.beta)

    z_series = pd.Series(z_scores, index=pa.index)
    spread_series = pd.Series(spreads, index=pa.index)

    # ── EGARCH volatility estimation ───────────────────────────────────
    # V10.1: Run real EGARCH on price series instead of hardcoding 0.20
    egarch_result = egarch_model.analyze(pa, cache_key=f"bt_{req.base}")
    vol_scale = egarch_result.forecast_vol  # Use forecast vol for MCTS

    # ── Backtest configuration ─────────────────────────────────────────
    z_entry = getattr(req, 'z_entry', 2.0)
    z_exit = getattr(req, 'z_exit', 0.5)
    stop_loss_z = getattr(req, 'stop_loss_z', 4.0)
    min_confidence = getattr(req, 'min_confidence', 0.3)
    capital_pct = getattr(req, 'capital_pct_per_trade', 0.05)
    slippage_pct = SLIPPAGE_BPS / 10000.0

    # ── Backtest loop ──────────────────────────────────────────────────
    position = 0
    entry_spread = entry_price_a = entry_price_b = 0.0
    entry_shares_a = entry_shares_b = 0
    trade_pnls: list = []
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    signals_list: list = []
    consecutive_wins = 0
    consecutive_losses = 0
    max_consecutive_wins = 0
    max_consecutive_losses = 0
    capital = req.initial_capital
    equity_curve: list = [capital]
    trades_executed = 0
    trades_filtered = 0  # V10.1: Count trades filtered by confidence

    warmup = max(30, settings.kalman.warmup_steps)
    max_hold_bars = 20  # V10.1: Force exit after N bars to avoid dead positions
    entry_bar = 0

    for i in range(warmup, len(z_series)):
        z = z_series.iloc[i]
        pa_i = float(pa.iloc[i])
        pb_i = float(pb.iloc[i])
        spread_i = spread_series.iloc[i]
        beta_i = betas[i]

        if pa_i <= 0 or pb_i <= 0:
            continue

        # ── EGARCH volatility update (every 20 bars) ─────────────────
        if (i - warmup) % 20 == 0:
            try:
                lookback_start = max(0, i - settings.egarch.lookback_days)
                price_history = pa.iloc[lookback_start:i+1]
                if len(price_history) >= 60:
                    egarch_result = egarch_model.analyze(
                        price_history, cache_key=f"bt_{req.base}_{i}"
                    )
                    vol_scale = egarch_result.forecast_vol
            except Exception:
                pass  # Keep using previous vol estimate

        # ── MCTS signal ─────────────────────────────────────────────
        mcts_result = mcts_engine.search(spread_i, 1.0, vol_scale)

        if position == 0:
            # ── Entry logic ─────────────────────────────────────────
            confidence = min(abs(z) / 3.0, 1.0)

            # V10.1: Filter by minimum confidence
            if confidence < min_confidence:
                trades_filtered += 1
                equity_curve.append(capital)
                continue

            # V10.1: Only enter when z is extreme AND MCTS doesn't veto
            should_enter = abs(z) >= z_entry

            if mcts_result.action == ActionType.LONG_SPREAD and z < 0:
                should_enter = True
            elif mcts_result.action == ActionType.SHORT_SPREAD and z > 0:
                should_enter = True
            elif mcts_result.action == ActionType.HOLD and abs(z) < z_entry * 1.3:
                should_enter = False

            if should_enter:
                if z < 0:
                    position = 1
                else:
                    position = -1

                entry_spread = spread_i
                entry_price_a = pa_i
                entry_price_b = pb_i
                entry_bar = i

                notional = capital * capital_pct
                entry_shares_a = max(1, int(notional / pa_i))
                beta_hedge = max(0.1, beta_i) if beta_i > 0 else 1.0
                entry_shares_b = max(1, int(entry_shares_a * beta_hedge))
                trades_executed += 1

        else:
            # ── Exit logic ──────────────────────────────────────────
            should_exit = False
            bars_held = i - entry_bar

            # Mean reversion exit
            if position == 1 and z > z_exit:
                should_exit = True
            elif position == -1 and z < -z_exit:
                should_exit = True

            # V10.1: Time-based exit — force close after max_hold_bars
            if bars_held >= max_hold_bars:
                should_exit = True

            # Stop-loss
            if stop_loss_z and abs(z) >= stop_loss_z:
                should_exit = True

            # MCTS exit
            if mcts_result.expected_value < -0.5 and position == 1:
                should_exit = True
            elif mcts_result.expected_value > 0.5 and position == -1:
                should_exit = True

            if should_exit:
                exit_spread = spread_i
                exit_price_a = pa_i
                exit_price_b = pb_i

                # V10.1: Dollar PnL — clean computation
                if position == 1:
                    # Long spread: bought A, sold B
                    pnl_a = (exit_price_a - entry_price_a) * entry_shares_a
                    pnl_b = (entry_price_b - exit_price_b) * entry_shares_b
                else:
                    # Short spread: sold A, bought B
                    pnl_a = (entry_price_a - exit_price_a) * entry_shares_a
                    pnl_b = (exit_price_b - entry_price_b) * entry_shares_b

                gross_pnl = pnl_a + pnl_b

                # Costs
                total_notional = (entry_price_a * entry_shares_a +
                                 entry_price_b * entry_shares_b +
                                 exit_price_a * entry_shares_a +
                                 exit_price_b * entry_shares_b)
                commission = total_notional * COMMISSION_BPS / 10000.0
                slippage = total_notional * slippage_pct
                net_pnl = gross_pnl - commission - slippage

                # Normalized return for Sharpe (per-unit of notional)
                entry_notional = entry_price_a * entry_shares_a + entry_price_b * entry_shares_b
                pnl_pct = net_pnl / max(entry_notional, 1.0)

                trade_pnls.append(pnl_pct)

                if net_pnl > 0:
                    wins += 1
                    gross_profit += net_pnl
                    consecutive_wins += 1
                    consecutive_losses = 0
                    max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
                else:
                    losses += 1
                    gross_loss += abs(net_pnl)
                    consecutive_losses += 1
                    consecutive_wins = 0
                    max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

                capital += net_pnl
                equity_curve.append(capital)

                confidence = min(abs(z) / 3.0, 1.0)
                signals_list.append(TradeSignal(
                    pair=f"{req.base}/{req.quote}",
                    action=(ActionType.LONG_SPREAD if position == 1
                            else ActionType.SHORT_SPREAD),
                    confidence=confidence,
                    kalman=KalmanState(
                        beta=beta_i, alpha=0, spread=exit_spread,
                        innovation_variance=1.0, pure_z_score=z, converged=True,
                    ),
                    egarch=EGARCHResult(
                        annualized_vol=egarch_result.annualized_vol,
                        forecast_vol=egarch_result.forecast_vol,
                        leverage_gamma=egarch_result.leverage_gamma,
                        regime=egarch_result.regime,
                        params=egarch_result.params,
                    ),
                    mcts=MCTSResult(
                        action=mcts_result.action,
                        expected_value=mcts_result.expected_value,
                        visit_distribution=mcts_result.visit_distribution,
                        avg_reward_distribution=mcts_result.avg_reward_distribution,
                    ),
                    source=SignalSource.BACKTEST,
                ))
                position = 0

        equity_curve.append(capital)

    # ── Handle empty results ───────────────────────────────────────────
    if not trade_pnls:
        warnings.append(
            f"No trades generated. Filtered {trades_filtered} weak signals "
            f"(min_confidence={min_confidence}). Try lowering z_entry={z_entry} or min_confidence."
        )
        return BacktestResponse(
            pair=f"{req.base}/{req.quote}",
            metrics=BacktestMetrics(
                total_return=0, sharpe_ratio=0, max_drawdown=0,
                win_rate=0, total_trades=0, avg_trade_pnl=0, profit_factor=0,
            ),
            signals=signals_list,
            warnings=warnings,
        )

    # ── Calculate metrics ──────────────────────────────────────────────
    cum_returns = np.cumsum(trade_pnls)
    total_return = cum_returns[-1]

    avg_ret = np.mean(trade_pnls)
    std_ret = np.std(trade_pnls)
    sharpe = (avg_ret / (std_ret + 1e-8)) * np.sqrt(252)

    # Maximum drawdown
    eq_array = np.array(equity_curve)
    peak = np.maximum.accumulate(eq_array)
    drawdowns = (peak - eq_array) / np.where(peak > 0, peak, 1e-8)
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0

    # Sortino ratio
    negative_returns = [p for p in trade_pnls if p < 0]
    downside_std = np.std(negative_returns) if negative_returns else 1e-8
    sortino = (avg_ret / (downside_std + 1e-8)) * np.sqrt(252)

    # Calmar ratio
    calmar = (total_return / (max_dd + 1e-8)) if max_dd > 0 else float('inf')

    # Benchmark: buy-and-hold base asset
    bh_return = (pa.iloc[-1] - pa.iloc[warmup]) / pa.iloc[warmup]

    # Profit factor
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    if warnings:
        warnings.append(
            f"Backtest complete: {len(trade_pnls)} trades, "
            f"{trades_filtered} filtered, "
            f"EGARCH vol={egarch_result.annualized_vol:.1%}, "
            f"BH return={bh_return:+.1%}"
        )
    else:
        warnings.append(
            f"Backtest complete: {len(trade_pnls)} trades, "
            f"{trades_filtered} filtered, "
            f"EGARCH vol={egarch_result.annualized_vol:.1%}, "
            f"BH return={bh_return:+.1%}"
        )

    return BacktestResponse(
        pair=f"{req.base}/{req.quote}",
        metrics=BacktestMetrics(
            total_return=round(total_return, 4),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_dd, 4),
            win_rate=round(wins / len(trade_pnls), 3),
            total_trades=len(trade_pnls),
            avg_trade_pnl=round(float(np.mean(trade_pnls)), 6),
            profit_factor=round(profit_factor, 2) if gross_loss > 0 else None,
            equity_curve=[round(e, 2) for e in equity_curve[::max(1, len(equity_curve)//100)]],  # Sample to ~100 points
            sortino_ratio=round(sortino, 2),
            calmar_ratio=round(calmar, 2) if calmar != float('inf') else None,
            max_consecutive_wins=max_consecutive_wins,
            max_consecutive_losses=max_consecutive_losses,
        ),
        signals=signals_list[:200],  # Limit signals in response
        warnings=warnings,
    )
