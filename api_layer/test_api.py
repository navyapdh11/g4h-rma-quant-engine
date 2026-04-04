"""
API Layer Tests — run with: python -m pytest api_layer/test_api.py -v
"""
import pytest
import asyncio
from datetime import datetime, timedelta


class TestYFinanceMarketData:
    """Test yfinance market data provider."""
    
    @pytest.fixture
    def provider(self):
        from api_layer.yfinance_api import YFinanceMarketData
        return YFinanceMarketData(retry_max=2)
    
    @pytest.mark.asyncio
    async def test_get_ohlcv_daily(self, provider):
        """Test daily OHLCV data fetch."""
        ohlcv = await provider.get_ohlcv("SPY", timeframe="1d", limit=30)
        
        assert ohlcv.symbol == "SPY"
        assert len(ohlcv.close) == 30
        assert all(ohlcv.close > 0)
        assert len(ohlcv.timestamp) == len(ohlcv.close)
    
    @pytest.mark.asyncio
    async def test_get_ohlcv_properties(self, provider):
        """Test OHLCV data properties."""
        ohlcv = await provider.get_ohlcv("QQQ", timeframe="1d", limit=50)
        
        # Test VWAP calculation
        assert len(ohlcv.vwap) == len(ohlcv.close)
        assert all(ohlcv.vwap > 0)
        
        # Test returns calculation
        assert len(ohlcv.returns) == len(ohlcv.close) - 1
    
    @pytest.mark.asyncio
    async def test_get_ticker(self, provider):
        """Test ticker fetch."""
        ticker = await provider.get_ticker("AAPL")
        
        assert ticker.symbol == "AAPL"
        assert ticker.bid > 0
        assert ticker.ask > 0
        assert ticker.last > 0
    
    @pytest.mark.asyncio
    async def test_is_available(self, provider):
        """Test health check."""
        available = await provider.is_available()
        assert available is True
    
    @pytest.mark.asyncio
    async def test_get_symbols(self, provider):
        """Test symbol list."""
        symbols = await provider.get_symbols("etf")
        assert "SPY" in symbols
        assert "QQQ" in symbols


class TestCCXTMarketData:
    """Test CCXT market data provider."""
    
    @pytest.fixture
    def provider(self):
        from api_layer.ccxt_api import CCXTMarketData
        return CCXTMarketData(exchange_id="binance", retry_max=2)
    
    @pytest.mark.asyncio
    async def test_get_ohlcv_crypto(self, provider):
        """Test crypto OHLCV data fetch."""
        ohlcv = await provider.get_ohlcv("BTC/USDT", timeframe="1d", limit=10)
        
        assert ohlcv.symbol == "BTC/USDT"
        assert len(ohlcv.close) <= 10
        assert all(ohlcv.close > 0)
    
    @pytest.mark.asyncio
    async def test_get_ticker_crypto(self, provider):
        """Test crypto ticker."""
        ticker = await provider.get_ticker("ETH/USDT")
        
        assert "ETH" in ticker.symbol
        assert ticker.bid > 0
        assert ticker.ask > 0


class TestAlpacaExecution:
    """Test Alpaca execution provider."""
    
    @pytest.fixture
    def provider(self):
        from api_layer.alpaca_api import AlpacaExecution
        # Test without API keys (simulation mode)
        return AlpacaExecution(paper=True)
    
    @pytest.mark.asyncio
    async def test_simulate_order(self, provider):
        """Test order simulation."""
        from api_layer.base import OrderSide, OrderType
        
        result = await provider.submit_order(
            symbol="SPY",
            side=OrderSide.BUY,
            qty=10,
            order_type=OrderType.MARKET,
            dry_run=True,
        )
        
        assert result.symbol == "SPY"
        assert result.side == OrderSide.BUY
        assert result.qty == 10
        assert result.status.value == "FILLED"
        assert result.avg_price > 0
    
    @pytest.mark.asyncio
    async def test_get_account_simulated(self, provider):
        """Test account info (simulated)."""
        account = await provider.get_account()
        
        # Should return error dict when no API keys
        assert "error" in account or "paper" in account


class TestFactory:
    """Test factory classes."""
    
    def test_market_data_factory_yfinance(self):
        """Test yfinance creation."""
        from api_layer.factory import MarketDataFactory
        
        provider = MarketDataFactory.create("yfinance")
        assert provider.name == "yfinance"
    
    def test_market_data_factory_ccxt(self):
        """Test CCXT creation."""
        from api_layer.factory import MarketDataFactory
        
        provider = MarketDataFactory.create("ccxt", exchange_id="binance")
        assert "ccxt" in provider.name
    
    def test_market_data_factory_invalid(self):
        """Test invalid provider."""
        from api_layer.factory import MarketDataFactory
        
        with pytest.raises(ValueError):
            MarketDataFactory.create("invalid_provider")
    
    def test_execution_factory_alpaca(self):
        """Test Alpaca creation."""
        from api_layer.factory import ExecutionFactory
        
        provider = ExecutionFactory.create("alpaca_paper")
        assert provider.is_paper is True
    
    def test_execution_factory_list(self):
        """Test provider listing."""
        from api_layer.factory import ExecutionFactory
        
        providers = ExecutionFactory.list_providers()
        assert len(providers) > 0


class TestOHLCVData:
    """Test OHLCV data structure."""
    
    def test_from_dataframe(self):
        """Test DataFrame conversion."""
        import pandas as pd
        import numpy as np
        from api_layer.base import OHLCVData
        
        df = pd.DataFrame({
            "open": [100, 101, 102],
            "high": [105, 106, 107],
            "low": [99, 100, 101],
            "close": [103, 104, 105],
            "volume": [1000, 1100, 1200],
        }, index=pd.date_range("2024-01-01", periods=3))
        
        ohlcv = OHLCVData.from_dataframe(df, "TEST")
        
        assert ohlcv.symbol == "TEST"
        assert len(ohlcv.close) == 3
        assert ohlcv.close[0] == 103
    
    def test_to_dataframe(self):
        """Test DataFrame export."""
        import numpy as np
        from api_layer.base import OHLCVData
        
        ohlcv = OHLCVData(
            symbol="TEST",
            timestamp=np.array(["2024-01-01", "2024-01-02"], dtype="datetime64"),
            open=np.array([100, 101]),
            high=np.array([105, 106]),
            low=np.array([99, 100]),
            close=np.array([103, 104]),
            volume=np.array([1000, 1100]),
        )
        
        df = ohlcv.to_dataframe()
        
        assert len(df) == 2
        assert "open" in df.columns
        assert "close" in df.columns


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
