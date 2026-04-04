"""Core validation tests — run with: python -m pytest tests/ -v"""
import numpy as np
import pytest


def test_kalman_convergence():
    from core.kalman import MultivariateKalmanFilter
    kf = MultivariateKalmanFilter()
    np.random.seed(42)
    for _ in range(500):
        pb = 50 + np.random.normal(0, 1)
        kf.step(2.0 * pb + np.random.normal(0, 0.5), pb)
    assert abs(kf.current_beta - 2.0) < 0.15


def test_kalman_symmetry():
    from core.kalman import MultivariateKalmanFilter
    kf = MultivariateKalmanFilter()
    for _ in range(100):
        kf.step(100 + np.random.normal(0, 2), 50 + np.random.normal(0, 1))
    assert np.allclose(kf.P, kf.P.T, atol=1e-10)


def test_kalman_warmup():
    from core.kalman import MultivariateKalmanFilter
    kf = MultivariateKalmanFilter()
    for _ in range(10):
        snap = kf.step(60.0, 50.0)
    assert not snap.converged
    for _ in range(30):
        snap = kf.step(60.0, 50.0)
    assert snap.converged


def test_mcts_hold_small_spread():
    from core.mcts import MCTSEngine
    engine = MCTSEngine()
    result = engine.search(0.1, 0.5, 1.0)
    assert result.action.value == "HOLD"


def test_pnl_directional():
    from core.mcts import MCTSEngine
    assert abs(MCTSEngine._compute_pnl(-2.0, 0.5, "LONG_SPREAD") - 2.5) < 1e-10
    assert abs(MCTSEngine._compute_pnl(2.5, 0.3, "SHORT_SPREAD") - 2.2) < 1e-10
    assert MCTSEngine._compute_pnl(1.0, 2.0, "HOLD") == 0.0


def test_risk_crisis_halt():
    from core.risk import RiskManager
    from models import TradeSignal, KalmanState, EGARCHResult, MCTSResult
    from models import ActionType, VolatilityRegime, SignalSource
    from datetime import datetime
    rm = RiskManager(crisis_regime_halt=True)
    sig = TradeSignal(
        pair="A/B", action=ActionType.SHORT_SPREAD, confidence=0.9,
        kalman=KalmanState(beta=1, alpha=0, spread=1, innovation_variance=1, pure_z_score=2.5),
        egarch=EGARCHResult(annualized_vol=0.5, forecast_vol=0.5, leverage_gamma=None,
                           regime=VolatilityRegime.CRISIS, params=None),
        mcts=MCTSResult(action=ActionType.SHORT_SPREAD, expected_value=0.5,
                       visit_distribution={}, avg_reward_distribution={}),
        source=SignalSource.KALMAN_MCTS,
    )
    ok, reason = rm.approve(sig)
    assert not ok
    assert "CRISIS" in reason


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
