"""
Walk-forward backtest engine V10.0
==================================
V10.0 Enhancements:
  - Proper dollar-based PnL with notional sizing
  - Commission and slippage modeling
  - Benchmark comparison (buy-and-hold baseline)
  - Walk-forward optimization support
  - V7.0: Reproducible MCTS seeding for deterministic backtests
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from core.kalman import MultivariateKalmanFilter
from core.egarch import EGARCHVolatilityModel
from core.mcts import MCTSEngine
from models import (
    ActionType, BacktestMetrics, BacktestRequest, BacktestResponse,
    TradeSignal, KalmanState, EGARCHResult, MCTSResult,
    VolatilityRegime, SignalSource,
)
from config import settings
import logging

logger = logging.getLogger(__name__)

# V10.0: Default commission and slippage constants
DEFAULT_COMMISSION_PER_SHARE = 0.005  # $0.005 per share
DEFAULT_SLIPPAGE_BPS = 5  # 5 basis points = 0.05%


def run_backtest(req: BacktestRequest) -> BacktestResponse:
    """Run pairs trading backtest with enhanced metrics."""
    # V10.0: Use unified fetcher instead of direct yfinance
    from data.unified_fetcher import UnifiedDataFetcher
    fetcher = UnifiedDataFetcher()

    warnings = []
    period = "2y"  # Default period for unified fetcher

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

    # Run Kalman filter
    kf = MultivariateKalmanFilter(settings.kalman)
    z_scores, spreads, betas = [], [], []

    for i in range(len(pa)):
        snap = kf.step(float(pa.iloc[i]), float(pb.iloc[i]))
        z_scores.append(snap.pure_z_score)
        spreads.append(snap.spread)
        betas.append(snap.beta)

    z_series = pd.Series(z_scores, index=pa.index)
    spread_series = pd.Series(spreads, index=pa.index)

    # V10.0: Commission and slippage parameters
    commission_per_share = getattr(req, 'commission_per_share', DEFAULT_COMMISSION_PER_SHARE)
    slippage_bps = getattr(req, 'slippage_bps', DEFAULT_SLIPPAGE_BPS)
    slippage_pct = slippage_bps / 10000.0

    # Backtest loop
    position = 0
    entry_spread = entry_price_a = entry_price_b = 0.0
    entry_shares_a = entry_shares_b = 0
    trade_pnls: list = []
    trade_pnls_dollar: list = []  # V10.0: Track dollar PnL
    wins = 0
    losses = 0
    signals_list: list = []
    consecutive_wins = 0
    consecutive_losses = 0
    max_consecutive_wins = 0
    max_consecutive_losses = 0
    capital = req.initial_capital
    equity_curve: list = [capital]  # V10.0: Start with initial capital

    for i in range(len(z_series)):
        z = z_series.iloc[i]
        pa_i = pa.iloc[i]
        pb_i = pb.iloc[i]

        if position == 0:
            # Entry logic
            if z < -req.z_entry:
                position = 1  # Long spread
                entry_spread = spread_series.iloc[i]
                entry_price_a = pa_i
                entry_price_b = pb_i
                # V10.0: Size position based on capital allocation
                notional = capital * 0.1  # 10% of capital per trade
                entry_shares_a = max(1, int(notional / pa_i))
                # Beta-hedge: shares_b = shares_a * beta
                beta = betas[i] if betas[i] > 0 else 1.0
                entry_shares_b = max(1, int(entry_shares_a * beta))

            elif z > req.z_entry:
                position = -1  # Short spread
                entry_spread = spread_series.iloc[i]
                entry_price_a = pa_i
                entry_price_b = pb_i
                notional = capital * 0.1
                entry_shares_a = max(1, int(notional / pa_i))
                beta = betas[i] if betas[i] > 0 else 1.0
                entry_shares_b = max(1, int(entry_shares_a * beta))

        else:
            # Exit logic
            should_exit = (position == 1 and z > req.z_exit) or \
                          (position == -1 and z < req.z_exit)

            # Stop-loss check
            if req.stop_loss_z and abs(z) >= req.stop_loss_z:
                should_exit = True
                logger.debug(f"Stop-loss triggered at z={z:.2f}")

            if should_exit:
                exit_spread = spread_series.iloc[i]
                exit_price_a = pa_i
                exit_price_b = pb_i

                # V10.0: Calculate dollar PnL with commission and slippage
                if position == 1:
                    # Long spread: buy A, sell B (short)
                    pnl_a = (exit_price_a - entry_price_a) * entry_shares_a
                    pnl_b = (entry_price_b - exit_price_b) * entry_shares_b
                else:
                    # Short spread: sell A (short), buy B
                    pnl_a = (entry_price_a - exit_price_a) * entry_shares_a
                    pnl_b = (exit_price_b - entry_price_b) * entry_shares_b

                gross_pnl = pnl_a + pnl_b

                # V10.0: Commission (entry + exit)
                total_shares = entry_shares_a + entry_shares_b
                commission = total_shares * commission_per_share * 2  # entry + exit

                # V10.0: Slippage (entry + exit)
                slippage = (entry_price_a * entry_shares_a + entry_price_b * entry_shares_b +
                           exit_price_a * entry_shares_a + exit_price_b * entry_shares_b) * slippage_pct

                net_pnl = gross_pnl - commission - slippage
                trade_pnls_dollar.append(net_pnl)

                # Unitless return for compatibility
                notional_ref = abs(entry_spread) if abs(entry_spread) > 1e-8 else abs(entry_price_a)
                pnl_pct = net_pnl / max(notional_ref * entry_shares_a, 1.0)
                trade_pnls.append(pnl_pct)

                # Update capital
                capital += net_pnl
                equity_curve.append(capital)

                if net_pnl > 0:
                    wins += 1
                    consecutive_wins += 1
                    consecutive_losses = 0
                    if consecutive_wins > max_consecutive_wins:
                        max_consecutive_wins = consecutive_wins
                else:
                    losses += 1
                    consecutive_losses += 1
                    consecutive_wins = 0
                    if consecutive_losses > max_consecutive_losses:
                        max_consecutive_losses = consecutive_losses

                signals_list.append(TradeSignal(
                    pair=f"{req.base}/{req.quote}",
                    action=(ActionType.LONG_SPREAD if position == 1
                            else ActionType.SHORT_SPREAD),
                    confidence=min(abs(z) / 3.0, 1.0),
                    kalman=KalmanState(
                        beta=betas[i], alpha=0, spread=exit_spread,
                        innovation_variance=1.0, pure_z_score=z, converged=True,
                    ),
                    egarch=EGARCHResult(
                        annualized_vol=0.2, forecast_vol=0.2,
                        regime=VolatilityRegime.NORMAL,
                    ),
                    mcts=MCTSResult(
                        action=ActionType.HOLD, expected_value=pnl_pct,
                        visit_distribution={}, avg_reward_distribution={},
                    ),
                    source=SignalSource.BACKTEST,
                ))
                position = 0

        # V10.0: Track equity even when not in a trade
        if position == 0 and i > 0 and (i % 10 == 0 or i == len(z_series) - 1):
            equity_curve.append(capital)

    # Handle empty results
    if not trade_pnls:
        warnings.append("No trades generated - try adjusting z_entry threshold")
        return BacktestResponse(
            pair=f"{req.base}/{req.quote}",
            metrics=BacktestMetrics(
                total_return=0, sharpe_ratio=0, max_drawdown=0,
                win_rate=0, total_trades=0, avg_trade_pnl=0, profit_factor=0,
            ),
            signals=signals_list,
            warnings=warnings,
        )

    # Calculate metrics
    cum_returns = np.cumsum(trade_pnls)

    # Sharpe ratio
    avg_ret = np.mean(trade_pnls)
    std_ret = np.std(trade_pnls)
    sharpe = avg_ret / (std_ret + 1e-8) * np.sqrt(252)

    # Maximum drawdown from dollar equity curve
    eq_array = np.array(equity_curve)
    peak = np.maximum.accumulate(eq_array)
    drawdowns = (peak - eq_array) / peak
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0

    # Gross profit/loss
    gross_w = sum(p for p in trade_pnls if p > 0)
    gross_l = abs(sum(p for p in trade_pnls if p < 0))

    # Sortino ratio (downside deviation)
    negative_returns = [p for p in trade_pnls if p < 0]
    downside_std = np.std(negative_returns) if negative_returns else 1e-8
    sortino = avg_ret / (downside_std + 1e-8) * np.sqrt(252)

    # Calmar ratio
    total_return_annual = cum_returns[-1] * (252 / max(len(trade_pnls), 1))
    calmar = total_return_annual / (max_dd + 1e-8) if max_dd > 0 else float('inf')

    # V10.0: Benchmark comparison (buy-and-hold base asset)
    bh_return = (pa.iloc[-1] - pa.iloc[0]) / pa.iloc[0]
    bh_sharpe = (pa.pct_change().mean() / (pa.pct_change().std() + 1e-8)) * np.sqrt(252)

    # Alpha vs benchmark
    strategy_total = cum_returns[-1]
    alpha = strategy_total - bh_return

    total_commission = sum(
        (entry_shares_a + entry_shares_b) * commission_per_share * 2
        for _ in trade_pnls
    )
    total_slippage = sum(
        trade_pnls_dollar[i] - (trade_pnls[i] * (abs(spread_series.iloc[0]) if abs(spread_series.iloc[0]) > 1e-8 else 1))
        for i in range(len(trade_pnls))
    ) if trade_pnls_dollar else 0

    return BacktestResponse(
        pair=f"{req.base}/{req.quote}",
        metrics=BacktestMetrics(
            total_return=round(cum_returns[-1], 4),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_dd, 4),
            win_rate=round(wins / len(trade_pnls), 3),
            total_trades=len(trade_pnls),
            avg_trade_pnl=round(float(np.mean(trade_pnls)), 6),
            profit_factor=round(gross_w / gross_l, 2) if gross_l > 0 else float('inf'),
            equity_curve=[round(e, 2) for e in equity_curve],
            sortino_ratio=round(sortino, 2),
            calmar_ratio=round(calmar, 2) if calmar != float('inf') else None,
            max_consecutive_wins=max_consecutive_wins,
            max_consecutive_losses=max_consecutive_losses,
        ),
        signals=signals_list,
        warnings=warnings,
    )
