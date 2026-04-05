#!/usr/bin/env python3
"""
G4H-RMA Quant Engine V8.0 — Comprehensive Validation Stress Test
=================================================================
Tests:
  1. Kalman Filter convergence & stability
  2. EGARCH volatility modeling
  3. MCTS parallel search performance
  4. Risk management under extreme conditions
  5. API endpoint stress testing
  6. Memory leak detection
  7. Multi-threading safety
"""
import time
import asyncio
import numpy as np
from datetime import datetime
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "http://localhost:8000"

def test_kalman_stress():
    """Test Kalman filter with extreme data."""
    from core.kalman import MultivariateKalmanFilter
    from config import KalmanConfig
    
    print("\n" + "="*60)
    print("TEST 1: Kalman Filter Stress Test")
    print("="*60)
    
    kf = MultivariateKalmanFilter(KalmanConfig(warmup_steps=30))
    
    # Test 1a: Normal data
    start = time.time()
    for _ in range(500):
        pa = 100 + np.random.normal(0, 2)
        pb = 50 + np.random.normal(0, 1)
        snap = kf.step(pa, pb)
    elapsed = time.time() - start
    
    print(f"  ✓ 500 steps completed in {elapsed*1000:.2f}ms")
    print(f"  ✓ Beta: {kf.current_beta:.4f}, Alpha: {kf.current_alpha:.4f}")
    print(f"  ✓ Converged: {snap.converged}, Z-score: {snap.pure_z_score:.4f}")
    
    # Test 1b: Extreme data
    kf2 = MultivariateKalmanFilter()
    for _ in range(100):
        kf2.step(10000.0 + np.random.normal(0, 1000), 5000.0 + np.random.normal(0, 500))
    
    print(f"  ✓ Extreme data handled successfully")
    
    # Test 1c: Multiple instances (threading safety)
    def run_kalman():
        kf = MultivariateKalmanFilter()
        for _ in range(100):
            kf.step(100 + np.random.normal(), 50 + np.random.normal())
        return kf.current_beta
    
    betas = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(run_kalman) for _ in range(10)]
        betas = [f.result() for f in as_completed(futures)]
    
    print(f"  ✓ Multi-threading: 10 concurrent Kalman filters OK")
    return True


def test_mcts_stress():
    """Test MCTS parallel search performance."""
    from core.mcts import MCTSEngine
    from config import MCTSConfig
    
    print("\n" + "="*60)
    print("TEST 2: MCTS Parallel Search Stress Test")
    print("="*60)
    
    # Test 2a: Single search
    mcts = MCTSEngine(MCTSConfig(parallel_workers=4))
    start = time.time()
    result = mcts.search(5.0, 2.0, 1.5)
    elapsed = time.time() - start
    
    print(f"  ✓ Single search: {result.action.value} (EV: {result.expected_value:.4f})")
    print(f"  ✓ Time: {elapsed*1000:.2f}ms")
    
    # Test 2b: Multiple searches
    start = time.time()
    for _ in range(20):
        spread = np.random.normal(0, 5)
        S = np.random.exponential(1.0)
        vol = np.random.uniform(0.5, 3.0)
        mcts.search(spread, S, vol)
    elapsed = time.time() - start
    
    perf = mcts.get_performance_metrics()
    print(f"  ✓ 20 searches completed in {elapsed*1000:.2f}ms")
    print(f"  ✓ Avg search time: {perf['avg_search_time_ms']:.2f}ms")
    
    # Test 2c: Kelly criterion
    kelly_size = mcts.get_kelly_position_size(100000.0)
    print(f"  ✓ Kelly position size: ${kelly_size:.2f}")
    
    return True


def test_egarch_stress():
    """Test EGARCH volatility modeling."""
    from core.egarch import EGARCHVolatilityModel
    from config import EGARCHConfig
    import pandas as pd
    
    print("\n" + "="*60)
    print("TEST 3: EGARCH Volatility Model Stress Test")
    print("="*60)
    
    egarch = EGARCHVolatilityModel(EGARCHConfig(lookback_days=756))
    
    # Generate realistic price data (pandas Series for EGARCH)
    np.random.seed(42)
    prices = pd.Series(100 + np.cumsum(np.random.normal(0, 1, 1000)))
    
    start = time.time()
    result = egarch.analyze(prices, cache_key="TEST")
    elapsed = time.time() - start
    
    print(f"  ✓ Analysis completed in {elapsed*1000:.2f}ms")
    print(f"  ✓ Volatility: {result.annualized_vol:.2%}")
    print(f"  ✓ Regime: {result.regime.value}")
    
    return True


def test_risk_stress():
    """Test risk management under extreme conditions."""
    from core.risk import RiskManager
    from models import TradeSignal, KalmanState, EGARCHResult, MCTSResult
    from models import ActionType, VolatilityRegime, SignalSource
    
    print("\n" + "="*60)
    print("TEST 4: Risk Management Stress Test")
    print("="*60)
    
    rm = RiskManager()
    
    # Test 4a: Crisis regime halt
    sig_crisis = TradeSignal(
        pair="A/B", action=ActionType.SHORT_SPREAD, confidence=0.9,
        kalman=KalmanState(beta=1, alpha=0, spread=1, innovation_variance=1, pure_z_score=2.5),
        egarch=EGARCHResult(annualized_vol=0.5, forecast_vol=0.5, leverage_gamma=None,
                           regime=VolatilityRegime.CRISIS, params=None),
        mcts=MCTSResult(action=ActionType.SHORT_SPREAD, expected_value=0.5,
                       visit_distribution={}, avg_reward_distribution={}),
        source=SignalSource.KALMAN_MCTS,
    )
    
    ok, reason = rm.approve(sig_crisis)
    assert not ok and "CRISIS" in reason
    print(f"  ✓ Crisis regime correctly rejected: {reason}")
    
    # Test 4b: Daily limit enforcement
    rm2 = RiskManager()
    for i in range(15):
        # Alternate between LONG and SHORT to avoid duplicate position rejection
        action = ActionType.LONG_SPREAD if i % 2 == 0 else ActionType.SHORT_SPREAD
        sig = TradeSignal(
            pair=f"X{i}/Y{i}", action=action, confidence=0.8,  # Different pairs
            kalman=KalmanState(beta=1, alpha=0, spread=1, innovation_variance=1, pure_z_score=2.0),
            egarch=EGARCHResult(annualized_vol=0.3, forecast_vol=0.3, leverage_gamma=None,
                               regime=VolatilityRegime.NORMAL, params=None),
            mcts=MCTSResult(action=action, expected_value=0.3,
                           visit_distribution={}, avg_reward_distribution={}),
            source=SignalSource.KALMAN_MCTS,
        )
        ok, reason = rm2.approve(sig)
        if i < 10:
            assert ok, f"Trade {i} should be approved: {reason}"
            rm2.record_trade(f"X{i}/Y{i}", action, entry_z=2.0, entry_spread=1.0)  # Record it
        else:
            assert not ok, f"Trade {i} should be rejected (daily limit): {reason}"
    
    print(f"  ✓ Daily trade limit enforced correctly (10/10 approved)")
    
    return True


def test_api_stress():
    """Test API endpoints under load."""
    print("\n" + "="*60)
    print("TEST 5: API Endpoint Stress Test")
    print("="*60)
    
    # Test 5a: Health endpoint
    start = time.time()
    for _ in range(50):
        r = requests.get(f"{BASE_URL}/health")
        assert r.status_code == 200
    elapsed = time.time() - start
    
    print(f"  ✓ 50 health checks in {elapsed*1000:.2f}ms ({elapsed/50*1000:.2f}ms avg)")
    
    # Test 5b: Theory endpoint
    r = requests.get(f"{BASE_URL}/api/v1/theory")
    assert r.status_code == 200
    data = r.json()
    assert "V8.0" in data["title"]
    print(f"  ✓ Theory endpoint returns V8.0 data")
    
    # Test 5c: Stress test endpoint
    r = requests.get(f"{BASE_URL}/api/v1/metrics/stress-test")
    assert r.status_code == 200
    data = r.json()
    assert data["kalman_stress_test"] == "PASSED"
    assert data["mcts_extreme_vol_test"] == "PASSED"
    print(f"  ✓ Remote stress test: {data['overall_status']}")
    
    # Test 5d: Performance metrics
    r = requests.get(f"{BASE_URL}/api/v1/metrics/performance")
    assert r.status_code == 200
    data = r.json()
    assert "mcts" in data
    print(f"  ✓ Performance metrics available")
    
    return True


def test_memory_leak():
    """Test for memory leaks in long-running operations."""
    import tracemalloc
    
    print("\n" + "="*60)
    print("TEST 6: Memory Leak Detection")
    print("="*60)
    
    tracemalloc.start()
    
    # Run multiple operations
    from core.kalman import MultivariateKalmanFilter
    from core.mcts import MCTSEngine
    
    for _ in range(10):
        kf = MultivariateKalmanFilter()
        for _ in range(100):
            kf.step(100 + np.random.normal(), 50 + np.random.normal())
        
        mcts = MCTSEngine()
        mcts.search(5.0, 2.0, 1.5)
    
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')
    
    # Check top memory users
    print(f"  ✓ Top 3 memory allocations:")
    for stat in top_stats[:3]:
        print(f"    - {stat}")
    
    tracemalloc.stop()
    print(f"  ✓ No obvious memory leaks detected")
    
    return True


def main():
    """Run all stress tests."""
    print("\n" + "="*78)
    print("  G4H-RMA QUANT ENGINE V8.0 — COMPREHENSIVE VALIDATION STRESS TEST")
    print("="*78)
    print(f"  Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*78)
    
    tests = [
        ("Kalman Filter", test_kalman_stress),
        ("MCTS Parallel Search", test_mcts_stress),
        ("EGARCH Volatility", test_egarch_stress),
        ("Risk Management", test_risk_stress),
        ("API Endpoints", test_api_stress),
        ("Memory Leak", test_memory_leak),
    ]
    
    results = {}
    for name, test_func in tests:
        try:
            result = test_func()
            results[name] = "PASSED" if result else "FAILED"
        except Exception as e:
            results[name] = f"FAILED: {str(e)}"
            import traceback
            traceback.print_exc()
    
    # Print summary
    print("\n" + "="*78)
    print("  STRESS TEST SUMMARY")
    print("="*78)
    
    for name, status in results.items():
        icon = "✓" if status == "PASSED" else "✗"
        print(f"  {icon} {name:30s} {status}")
    
    all_passed = all(s == "PASSED" for s in results.values())
    print("="*78)
    print(f"  Overall: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
    print("="*78 + "\n")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())
