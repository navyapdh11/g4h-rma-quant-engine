#!/usr/bin/env python3
"""
G4H-RMA Quant Engine — Python Client Snippet
=============================================
Scan and trade the 15 premium liquid equity pairs.

Usage:
    python client_snippet.py
"""
import requests
import json
from typing import List, Dict, Any

BASE_URL = "http://localhost:8000"

# Premium liquid pairs (from pairs.json)
LIQUID_PAIRS = [
    {"base": "AAPL", "quote": "MSFT"},
    {"base": "NVDA", "quote": "AMD"},
    {"base": "GOOGL", "quote": "META"},
    {"base": "AMZN", "quote": "GOOGL"},
    {"base": "XOM", "quote": "CVX"},
    {"base": "JPM", "quote": "BAC"},
    {"base": "V", "quote": "MA"},
    {"base": "KO", "quote": "PEP"},
    {"base": "WMT", "quote": "COST"},
    {"base": "PG", "quote": "KMB"},
    {"base": "MCD", "quote": "YUM"},
    {"base": "GS", "quote": "MS"},
    {"base": "DAL", "quote": "UAL"},
    {"base": "PFE", "quote": "JNJ"},
    {"base": "LLY", "quote": "ABBV"},
]


def scan_all_pairs(mcts_iterations: int = 1200) -> Dict[str, Any]:
    """Scan all 15 liquid pairs."""
    response = requests.post(
        f"{BASE_URL}/api/v1/scan",
        json={
            "pairs": LIQUID_PAIRS,
            "mcts_iterations": mcts_iterations,
            "source": "KALMAN_MCTS"
        },
        timeout=120
    )
    response.raise_for_status()
    return response.json()


def scan_single_pair(base: str, quote: str, iterations: int = 800) -> Dict[str, Any]:
    """Scan a single pair."""
    pair = f"{base}_{quote}"
    response = requests.get(
        f"{BASE_URL}/api/v1/scan/{pair}",
        params={"iterations": iterations},
        timeout=60
    )
    response.raise_for_status()
    return response.json()


def execute_trade(base: str, quote: str, action: str, dry_run: bool = True) -> Dict[str, Any]:
    """Execute a trade."""
    response = requests.post(
        f"{BASE_URL}/api/v1/execute",
        json={
            "base": base,
            "quote": quote,
            "action": action,
            "dry_run": dry_run
        },
        timeout=60
    )
    response.raise_for_status()
    return response.json()


def get_account() -> Dict[str, Any]:
    """Get account information."""
    response = requests.get(f"{BASE_URL}/api/v1/account", timeout=30)
    response.raise_for_status()
    return response.json()


def get_agents_status() -> Dict[str, Any]:
    """Get multi-agent system status."""
    response = requests.get(f"{BASE_URL}/api/v1/agents/status", timeout=30)
    response.raise_for_status()
    return response.json()


def print_scan_results(scan_result: Dict[str, Any]):
    """Pretty print scan results."""
    print("\n" + "="*80)
    print("SCAN RESULTS")
    print("="*80)
    
    signals = scan_result.get("signals", [])
    errors = scan_result.get("errors", [])
    
    # Sort by confidence (highest first)
    signals.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    
    print(f"\n{'Pair':<15} {'Signal':<15} {'Confidence':<12} {'Z-Score':<10} {'Action':<12}")
    print("-"*80)
    
    for signal in signals:
        pair = signal.get("pair", "N/A")
        action = signal.get("action", "N/A")
        confidence = signal.get("confidence", 0) * 100
        z_score = signal.get("kalman", {}).get("pure_z_score", 0)
        
        # Color coding
        signal_emoji = "🟢" if action in ["LONG_SPREAD", "SHORT_SPREAD"] and confidence > 0.6 else "🟡"
        
        print(f"{pair:<15} {action:<15} {confidence:>10.1f}%  {z_score:>+10.2f} {signal_emoji}")
    
    if errors:
        print(f"\n⚠️  Errors: {len(errors)}")
        for err in errors:
            print(f"   - {err}")
    
    print("="*80)


def find_best_opportunities(scan_result: Dict[str, Any], min_confidence: float = 0.6) -> List[Dict]:
    """Find high-confidence trading opportunities."""
    signals = scan_result.get("signals", [])
    
    opportunities = [
        s for s in signals
        if s.get("confidence", 0) >= min_confidence
        and s.get("action") in ["LONG_SPREAD", "SHORT_SPREAD"]
    ]
    
    opportunities.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return opportunities


def main():
    """Main demo function."""
    print("🚀 G4H-RMA Quant Engine — Premium Pairs Scanner")
    print("="*60)
    
    # Check health
    try:
        health = requests.get(f"{BASE_URL}/health", timeout=10).json()
        print(f"✓ Server: {health.get('version', 'Unknown')}")
        print(f"✓ Uptime: {health.get('uptime_seconds', 0):.0f}s")
    except Exception as e:
        print(f"✗ Server not reachable: {e}")
        return
    
    # Check account
    try:
        account = get_account()
        if account.get("status") == "SIMULATION":
            print("⚠️  Mode: SIMULATION (no API keys configured)")
        else:
            print(f"✓ Account: {account.get('status', 'Unknown')} | Cash: ${account.get('cash', 0):,.0f}")
    except Exception as e:
        print(f"⚠️  Account info unavailable: {e}")
    
    # Scan all pairs
    print("\n📊 Scanning 15 premium liquid pairs...")
    print("   This may take 30-60 seconds...")
    
    try:
        result = scan_all_pairs(mcts_iterations=1200)
        print_scan_results(result)
        
        # Find best opportunities
        opportunities = find_best_opportunities(result, min_confidence=0.5)
        
        if opportunities:
            print(f"\n🎯 Top {len(opportunities)} Opportunities:")
            for i, opp in enumerate(opportunities[:5], 1):
                print(f"   {i}. {opp['pair']}: {opp['action']} (conf: {opp['confidence']*100:.0f}%)")
            
            # Ask if user wants to trade
            if opportunities and input("\nExecute top opportunity? (y/n): ").lower() == 'y':
                top = opportunities[0]
                base, quote = top['pair'].split('/')
                action = top['action']
                
                print(f"\nExecuting: {action} {base}/{quote} (dry-run)...")
                trade_result = execute_trade(base, quote, action, dry_run=True)
                print(f"Result: {trade_result.get('status', 'Unknown')}")
                print(f"Details: {trade_result.get('details', 'N/A')}")
        
    except requests.Timeout:
        print("⏱️  Scan timed out — try reducing mcts_iterations or checking network")
    except Exception as e:
        print(f"✗ Scan failed: {e}")


if __name__ == "__main__":
    main()
