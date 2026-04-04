# API Layer Documentation

## Overview

The G4H-RMA Quant Engine now includes a unified **API Layer** that provides clean abstractions for market data and execution across multiple providers.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                        │
│  (main.py, app.py, backtest.py, strategies)                │
├─────────────────────────────────────────────────────────────┤
│                    Factory Layer                            │
│  MarketDataFactory  │  ExecutionFactory                     │
├─────────────────────────────────────────────────────────────┤
│                 Abstract Base Classes                       │
│  MarketDataProvider  │  ExecutionProvider                   │
├─────────────────────────────────────────────────────────────┤
│                  Provider Implementations                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐     │
│  │  yfinance   │  │    CCXT     │  │     Alpaca      │     │
│  │  (equities) │  │  (crypto)   │  │ (execution +    │     │
│  │             │  │             │  │     data)       │     │
│  └─────────────┘  └─────────────┘  └─────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### Market Data

```python
from api_layer import MarketDataFactory

# Create provider
md = MarketDataFactory.create("yfinance")

# Get OHLCV data
ohlcv = await md.get_ohlcv("SPY", timeframe="1d", limit=100)

# Access data
print(f"Symbol: {ohlcv.symbol}")
print(f"Close prices: {ohlcv.close[-5:]}")
print(f"VWAP: {ohlcv.vwap[-1]:.2f}")
print(f"Returns: {ohlcv.returns[-1]:.4f}")

# Convert to DataFrame
df = ohlcv.to_dataframe()
```

### Execution

```python
from api_layer import ExecutionFactory, OrderSide, OrderType

# Create provider (paper trading by default)
exec = ExecutionFactory.create("alpaca_paper")

# Submit order
result = await exec.submit_order(
    symbol="SPY",
    side=OrderSide.BUY,
    qty=10,
    order_type=OrderType.MARKET,
    dry_run=True,  # Simulation
)

print(f"Status: {result.status}")
print(f"Filled: {result.filled_qty} @ ${result.avg_price:.2f}")
```

## Available Providers

### Market Data

| Provider | Code | Assets | Notes |
|----------|------|--------|-------|
| Yahoo Finance | `yfinance`, `yahoo`, `yf` | Equity, ETF, Crypto | Adjusted prices |
| CCXT | `ccxt:*` | Crypto | 100+ exchanges |
| Alpaca Data | `alpaca-data` | Equity, Crypto | Real-time quotes |

### Execution

| Provider | Code | Mode | Notes |
|----------|------|------|-------|
| Alpaca Paper | `alpaca_paper`, `alpaca` | Simulated | Free paper trading |
| Alpaca Live | `alpaca_live` | Real money | Requires API keys |

## API Reference

### MarketDataProvider

```python
class MarketDataProvider(ABC):
    @property
    def name(self) -> str: ...
    
    @property
    def supported_assets(self) -> List[str]: ...
    
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> OHLCVData: ...
    
    async def get_ticker(self, symbol: str) -> Ticker: ...
    
    async def get_symbols(self, asset_class: Optional[str] = None) -> List[str]: ...
    
    async def is_available(self) -> bool: ...
```

### ExecutionProvider

```python
class ExecutionProvider(ABC):
    @property
    def name(self) -> str: ...
    
    @property
    def is_paper(self) -> bool: ...
    
    async def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: str = "gtc",
        dry_run: bool = False,
    ) -> OrderResult: ...
    
    async def cancel_order(self, order_id: str) -> bool: ...
    
    async def get_position(self, symbol: str) -> Optional[Position]: ...
    
    async def get_positions(self) -> List[Position]: ...
    
    async def get_account(self) -> Dict[str, Any]: ...
```

## Data Structures

### OHLCVData

```python
@dataclass
class OHLCVData:
    symbol: str
    timestamp: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    
    # Computed properties
    @property
    def vwap(self) -> np.ndarray: ...
    
    @property
    def returns(self) -> np.ndarray: ...
    
    # Conversion
    def to_dataframe(self) -> pd.DataFrame: ...
    
    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, symbol: str) -> "OHLCVData": ...
```

### OrderResult

```python
@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    filled_qty: float
    avg_price: float
    status: OrderStatus
    timestamp: datetime
    commission: float = 0.0
    raw_response: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    
    @property
    def is_filled(self) -> bool: ...
    
    @property
    def notional_value(self) -> float: ...
```

### Position

```python
@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    
    @property
    def is_long(self) -> bool: ...
    
    @property
    def is_short(self) -> bool: ...
```

## Enums

```python
class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"

class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"

class VolatilityRegime(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    CRISIS = "CRISIS"
```

## Examples

### Multi-Provider Strategy

```python
from api_layer import MarketDataFactory, ExecutionFactory

# Use yfinance for equities
equity_md = MarketDataFactory.create("yfinance")

# Use CCXT for crypto
crypto_md = MarketDataFactory.create("ccxt", exchange_id="binance")

# Execute on Alpaca
executor = ExecutionFactory.create("alpaca_paper")

# Get data from both
spy_data = await equity_md.get_ohlcv("SPY", limit=100)
btc_data = await crypto_md.get_ohlcv("BTC/USDT", limit=100)

# Execute based on signals
if should_buy_spy(spy_data):
    await executor.submit_order("SPY", OrderSide.BUY, qty=10)
```

### Custom Provider

```python
from api_layer.base import MarketDataProvider, OHLCVData
from api_layer.factory import MarketDataFactory

class MyCustomProvider(MarketDataProvider):
    @property
    def name(self) -> str:
        return "my_custom"
    
    @property
    def supported_assets(self) -> List[str]:
        return ["equity"]
    
    async def get_ohlcv(self, symbol, timeframe="1d", **kwargs) -> OHLCVData:
        # Your implementation
        ...
    
    async def get_ticker(self, symbol: str) -> Ticker:
        ...
    
    async def get_symbols(self, asset_class=None) -> List[str]:
        ...
    
    async def is_available(self) -> bool:
        ...

# Register and use
MarketDataFactory.register("my_custom", MyCustomProvider)
provider = MarketDataFactory.create("my_custom")
```

## Error Handling

All providers implement retry logic with exponential backoff:

```python
provider = MarketDataFactory.create("yfinance", retry_max=5, retry_delay=1.0)

try:
    data = await provider.get_ohlcv("SPY")
except RuntimeError as e:
    print(f"Failed after retries: {e}")
```

## Rate Limiting

CCXT providers have built-in rate limiting:

```python
# Enable rate limiting (default: True)
provider = MarketDataFactory.create(
    "ccxt",
    exchange_id="binance",
    rate_limit=True,
)
```

## Testing

```bash
# Run API layer tests
python -m pytest api_layer/test_api.py -v

# Run specific test class
python -m pytest api_layer/test_api.py::TestYFinanceMarketData -v
```

## Migration Guide

### From old data/fetcher.py

**Before:**
```python
from data.fetcher import DataFetcher

fetcher = DataFetcher()
df = await fetcher.get_yfinance("SPY", "QQQ")
```

**After:**
```python
from api_layer import MarketDataFactory

md = MarketDataFactory.create("yfinance")
spy_data = await md.get_ohlcv("SPY", limit=500)
qqq_data = await md.get_ohlcv("QQQ", limit=500)
```

The old `data/fetcher.py` still works but new code should use the API layer.

## Configuration

Set environment variables for Alpaca:

```bash
export APCA_API_KEY_ID="your_key_here"
export APCA_API_SECRET_KEY="your_secret_here"
```

## Files

```
api_layer/
├── __init__.py         # Exports
├── base.py             # Abstract base classes, data structures
├── yfinance_api.py     # Yahoo Finance implementation
├── ccxt_api.py         # CCXT crypto implementation
├── alpaca_api.py       # Alpaca execution + data
├── factory.py          # Factory classes
└── test_api.py         # Unit tests
```
