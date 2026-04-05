#!/usr/bin/env python3
"""
Local Historical Data Loader V9.0 — Fixed
==========================================
Generates realistic OHLCV data for all symbols using GBM.
Compatible with the engine's fetcher interface.
"""
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import numpy as np
import pandas as pd

# Real market parameters for all symbols
SYMBOL_PARAMS = {
    # US Equities
    "AAPL":  {"price": 195.0, "vol": 0.25, "drift": 0.15},
    "MSFT":  {"price": 420.0, "vol": 0.22, "drift": 0.12},
    "NVDA":  {"price": 880.0, "vol": 0.45, "drift": 0.40},
    "AMD":   {"price": 180.0, "vol": 0.42, "drift": 0.25},
    "GOOGL": {"price": 175.0, "vol": 0.25, "drift": 0.15},
    "META":  {"price": 510.0, "vol": 0.30, "drift": 0.20},
    "AMZN":  {"price": 185.0, "vol": 0.28, "drift": 0.18},
    "XOM":   {"price": 105.0, "vol": 0.22, "drift": 0.05},
    "CVX":   {"price": 155.0, "vol": 0.20, "drift": 0.04},
    "JPM":   {"price": 200.0, "vol": 0.20, "drift": 0.12},
    "BAC":   {"price": 38.0,  "vol": 0.24, "drift": 0.10},
    "V":     {"price": 280.0, "vol": 0.18, "drift": 0.10},
    "MA":    {"price": 460.0, "vol": 0.18, "drift": 0.12},
    "KO":    {"price": 62.0,  "vol": 0.14, "drift": 0.05},
    "PEP":   {"price": 175.0, "vol": 0.15, "drift": 0.06},
    "WMT":   {"price": 165.0, "vol": 0.16, "drift": 0.08},
    "COST":  {"price": 750.0, "vol": 0.18, "drift": 0.12},
    "PG":    {"price": 165.0, "vol": 0.14, "drift": 0.05},
    "KMB":   {"price": 140.0, "vol": 0.15, "drift": 0.04},
    "MCD":   {"price": 295.0, "vol": 0.18, "drift": 0.06},
    "YUM":   {"price": 145.0, "vol": 0.18, "drift": 0.08},
    "GS":    {"price": 470.0, "vol": 0.22, "drift": 0.12},
    "MS":    {"price": 100.0, "vol": 0.24, "drift": 0.10},
    "DAL":   {"price": 52.0,  "vol": 0.28, "drift": 0.08},
    "UAL":   {"price": 50.0,  "vol": 0.32, "drift": 0.06},
    "PFE":   {"price": 28.0,  "vol": 0.25, "drift": -0.02},
    "JNJ":   {"price": 155.0, "vol": 0.15, "drift": 0.03},
    "LLY":   {"price": 780.0, "vol": 0.30, "drift": 0.35},
    "ABBV":  {"price": 175.0, "vol": 0.22, "drift": 0.08},
    # International ADRs
    "TSM":   {"price": 160.0, "vol": 0.30, "drift": 0.20},
    "ASML":  {"price": 950.0, "vol": 0.28, "drift": 0.15},
    "BABA":  {"price": 78.0,  "vol": 0.35, "drift": 0.05},
    "PDD":   {"price": 120.0, "vol": 0.40, "drift": 0.15},
    "JD":    {"price": 32.0,  "vol": 0.35, "drift": 0.02},
    "TM":    {"price": 200.0, "vol": 0.20, "drift": 0.08},
    "HMC":   {"price": 36.0,  "vol": 0.22, "drift": 0.06},
    "SHEL":  {"price": 68.0,  "vol": 0.22, "drift": 0.04},
    "BP":    {"price": 36.0,  "vol": 0.24, "drift": 0.03},
    "AZN":   {"price": 70.0,  "vol": 0.20, "drift": 0.08},
    "NVS":   {"price": 105.0, "vol": 0.18, "drift": 0.06},
    "HSBC":  {"price": 42.0,  "vol": 0.22, "drift": 0.05},
    "ING":   {"price": 18.0,  "vol": 0.25, "drift": 0.04},
    "BHP":   {"price": 58.0,  "vol": 0.25, "drift": 0.03},
    "RIO":   {"price": 68.0,  "vol": 0.24, "drift": 0.02},
    "VALE":  {"price": 12.0,  "vol": 0.30, "drift": 0.01},
    "INFY":  {"price": 20.0,  "vol": 0.22, "drift": 0.06},
    "WIT":   {"price": 7.0,   "vol": 0.25, "drift": 0.04},
    "SONY":  {"price": 92.0,  "vol": 0.25, "drift": 0.08},
    "NTDOY": {"price": 12.0,  "vol": 0.22, "drift": 0.05},
    "MUFG":  {"price": 10.0,  "vol": 0.22, "drift": 0.04},
    "SMFG":  {"price": 13.0,  "vol": 0.24, "drift": 0.03},
    "UL":    {"price": 55.0,  "vol": 0.15, "drift": 0.04},
    "NSRGY": {"price": 105.0, "vol": 0.16, "drift": 0.05},
    "DEO":   {"price": 220.0, "vol": 0.18, "drift": 0.06},
    "BUD":   {"price": 48.0,  "vol": 0.20, "drift": 0.03},
    "GSK":   {"price": 38.0,  "vol": 0.18, "drift": 0.05},
}


def generate_symbol_data(symbol: str, period: str = "2y",
                         data_dir: str = None) -> Optional[pd.DataFrame]:
    """
    Generate OHLCV data for a symbol using GBM.
    Returns DataFrame indexed by business days with OHLCV columns.
    """
    import hashlib
    params = SYMBOL_PARAMS.get(symbol)
    if params is None:
        return None

    years = int(period.replace('mo', '0').replace('y', '')) if 'y' in period else 2
    if 'mo' in period:
        years = int(period.replace('mo', '')) / 12

    # Generate business days
    end_date = pd.Timestamp.now()
    start_date = end_date - pd.DateOffset(years=years)
    dates = pd.bdate_range(start=start_date, end=end_date)
    n = len(dates)

    # Seed for reproducibility
    seed = int(hashlib.md5(f"g4h_{symbol}_{period}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)

    # GBM parameters
    S0 = params["price"]
    mu = params["drift"]
    sigma = params["vol"]
    dt = 1.0 / 252.0

    # Generate returns with mean-reversion
    z = rng.standard_normal(n)
    returns = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z
    close_prices = S0 * np.exp(np.cumsum(returns))

    # Generate OHLC from close
    intraday_range = sigma * np.sqrt(dt) * 0.4
    open_prices = close_prices * (1 + rng.normal(0, intraday_range * 0.5, n))
    high_prices = np.maximum(close_prices, open_prices) * (1 + np.abs(rng.normal(0, intraday_range * 0.3, n)))
    low_prices = np.minimum(close_prices, open_prices) * (1 - np.abs(rng.normal(0, intraday_range * 0.3, n)))

    # Generate volume (higher for volatile stocks, weekday seasonality)
    base_vol = int(2e6 / max(sigma, 0.1))
    weekday_factor = np.array([1.2 if d.weekday() == 4 else 0.9 if d.weekday() == 0 else 1.0 for d in dates])
    volumes = (base_vol * weekday_factor * np.exp(rng.normal(0, 0.3, n))).astype(int)

    df = pd.DataFrame({
        "Open": np.round(open_prices, 2),
        "High": np.round(high_prices, 2),
        "Low": np.round(low_prices, 2),
        "Close": np.round(close_prices, 2),
        "Volume": volumes,
    }, index=dates)
    df.index.name = "Date"

    # Ensure OHLC consistency
    df["High"] = df[["High", "Close"]].max(axis=1)
    df["Low"] = df[["Low", "Close"]].min(axis=1)

    # Save to CSV
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        csv_path = os.path.join(data_dir, f"{symbol}_{period}.csv")
        df.to_csv(csv_path)

    return df


def get_paired_data(symbol_a: str, symbol_b: str, period: str = "2y",
                    data_dir: str = None) -> Optional[pd.DataFrame]:
    """Get aligned paired data for two symbols."""
    if data_dir is None:
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "historical")

    df_a = generate_symbol_data(symbol_a, period, data_dir)
    df_b = generate_symbol_data(symbol_b, period, data_dir)

    if df_a is None or df_b is None:
        return None

    # Extract Close and rename
    pa = df_a[["Close"]].rename(columns={"Close": symbol_a})
    pb = df_b[["Close"]].rename(columns={"Close": symbol_b})

    # Inner join on dates
    aligned = pa.join(pb, how="inner")
    if len(aligned) < 30:
        return None
    return aligned


if __name__ == "__main__":
    import sys
    data_dir = os.path.join(os.path.dirname(__file__), "historical")

    if len(sys.argv) > 1 and sys.argv[1] == "--generate":
        print(f"Generating historical data for {len(SYMBOL_PARAMS)} symbols...")
        for sym in sorted(SYMBOL_PARAMS):
            df = generate_symbol_data(sym, "2y", data_dir)
            if df is not None:
                print(f"  ✅ {sym:<8} {len(df):>4} rows  ${df['Close'].iloc[-1]:>9.2f}  latest: {df.index[-1].date()}")
        print(f"\nData saved to: {data_dir}")
    elif len(sys.argv) > 1:
        symbol = sys.argv[1]
        period = sys.argv[2] if len(sys.argv) > 2 else "2y"
        df = generate_symbol_data(symbol, period, data_dir)
        if df is not None:
            print(f"\n{symbol} ({period}): {len(df)} rows")
            print(f"Range: {df.index[0].date()} → {df.index[-1].date()}")
            print(f"Close: ${df['Close'].min():.2f} → ${df['Close'].max():.2f}")
            print(f"\nLast 5 days:")
            print(df.tail().to_string())
        else:
            print(f"No params for {symbol}")
    else:
        print("Local Historical Data Loader V9.0")
        print(f"Symbols: {len(SYMBOL_PARAMS)}")
        print(f"Data dir: {data_dir}")
        print("Usage: python local_data.py --generate | python local_data.py AAPL [period]")
