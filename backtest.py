"""
Walk-forward backtest engine V6.0
==================================
Enhanced with:
  - Stop-loss support
  - Enhanced metrics (Sortino, Calmar)
  - Better error handling
  - Consecutive win/loss tracking
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


def run_backtest(req: BacktestRequest) -> BacktestResponse:
    """Run pairs trading backtest with enhanced metrics."""
    import yfinance as yf
    
    warnings = []
    end = req.end or pd.Timestamp.now().strftime("%Y-%m-%d")
    
    try:
        data = yf.download([req.base, req.quote], start=req.start, end=end,
                           progress=False, auto_adjust=False)
    except Exception as e:
        logger.error(f"Data fetch failed: {e}")
        warnings.append(f"Data fetch warning: {str(e)}")
        # Return empty result
        return BacktestResponse(
            pair=f"{req.base}/{req.quote}",
            metrics=BacktestMetrics(
                total_return=0, sharpe_ratio=0, max_drawdown=0,
                win_rate=0, total_trades=0, avg_trade_pnl=0, profit_factor=0,
            ),
            warnings=warnings,
        )
    
    # Handle yfinance multi-level column structure
    if isinstance(data.columns, pd.MultiIndex):
        # Try to get Adj Close first, fall back to Close
        if "Adj Close" in data.columns.get_level_values(0):
            data = data["Adj Close"].dropna()
        elif "Close" in data.columns.get_level_values(0):
            data = data["Close"].dropna()
        else:
            warnings.append(f"No price data found for {req.base}/{req.quote}")
            return BacktestResponse(
                pair=f"{req.base}/{req.quote}",
                metrics=BacktestMetrics(
                    total_return=0, sharpe_ratio=0, max_drawdown=0,
                    win_rate=0, total_trades=0, avg_trade_pnl=0, profit_factor=0,
                ),
                warnings=warnings,
            )
    else:
        # Single-level columns
        price_col = "Adj Close" if "Adj Close" in data.columns else "Close"
        data = data[price_col].dropna()

    # Check data availability
    if req.base not in data.columns or req.quote not in data.columns:
        warnings.append(f"Missing data for one or both symbols")
        return BacktestResponse(
            pair=f"{req.base}/{req.quote}",
            metrics=BacktestMetrics(
                total_return=0, sharpe_ratio=0, max_drawdown=0,
                win_rate=0, total_trades=0, avg_trade_pnl=0, profit_factor=0,
            ),
            warnings=warnings,
        )
    
    pa, pb = data[req.base], data[req.quote]
    
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
    
    # Backtest loop
    position = 0
    entry_spread = entry_price_a = 0.0
    trade_pnls: list = []
    wins = 0
    losses = 0
    signals_list: list = []
    consecutive_wins = 0
    consecutive_losses = 0
    max_consecutive_wins = 0
    max_consecutive_losses = 0
    
    for i in range(len(z_series)):
        z = z_series.iloc[i]
        pa_i = pa.iloc[i]
        
        if position == 0:
            # Entry logic
            if z < -req.z_entry:
                position = 1  # Long spread
                entry_spread = spread_series.iloc[i]
                entry_price_a = pa_i
            elif z > req.z_entry:
                position = -1  # Short spread
                entry_spread = spread_series.iloc[i]
                entry_price_a = pa_i
        
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
                pnl = ((exit_spread - entry_spread) if position == 1
                       else (entry_spread - exit_spread)) / entry_price_a
                trade_pnls.append(pnl)
                
                if pnl > 0:
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
                        action=ActionType.HOLD, expected_value=pnl,
                        visit_distribution={}, avg_reward_distribution={},
                    ),
                    source=SignalSource.BACKTEST,
                ))
                position = 0
    
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
    cum = np.cumsum(trade_pnls)
    
    # Sharpe ratio
    sharpe = np.mean(trade_pnls) / (np.std(trade_pnls) + 1e-8) * np.sqrt(252)
    
    # Maximum drawdown
    max_dd = abs(np.min(cum - np.maximum.accumulate(cum)))
    
    # Gross profit/loss
    gross_w = sum(p for p in trade_pnls if p > 0)
    gross_l = abs(sum(p for p in trade_pnls if p < 0))
    
    # Sortino ratio (downside deviation)
    negative_returns = [p for p in trade_pnls if p < 0]
    downside_std = np.std(negative_returns) if negative_returns else 1e-8
    sortino = np.mean(trade_pnls) / (downside_std + 1e-8) * np.sqrt(252)
    
    # Calmar ratio
    calmar = np.mean(trade_pnls) * 252 / (max_dd + 1e-8) if max_dd > 0 else float('inf')
    
    # Equity curve
    equity = req.initial_capital * (1 + cum)
    
    return BacktestResponse(
        pair=f"{req.base}/{req.quote}",
        metrics=BacktestMetrics(
            total_return=round(cum[-1], 4),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_dd, 4),
            win_rate=round(wins / len(trade_pnls), 3),
            total_trades=len(trade_pnls),
            avg_trade_pnl=round(float(np.mean(trade_pnls)), 6),
            profit_factor=round(gross_w / gross_l, 2) if gross_l > 0 else float('inf'),
            equity_curve=[round(e, 2) for e in equity.tolist()],
            sortino_ratio=round(sortino, 2),
            calmar_ratio=round(calmar, 2) if calmar != float('inf') else None,
            max_consecutive_wins=max_consecutive_wins,
            max_consecutive_losses=max_consecutive_losses,
        ),
        signals=signals_list,
        warnings=warnings,
    )
