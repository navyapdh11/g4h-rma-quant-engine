"""
G4H-RMA Quant Engine V6.1 — Unified Connection Manager
=======================================================
Manages all broker/exchange connections:
  - Alpaca (US equities)
  - Binance (crypto spot + futures)
  - Bybit (crypto spot + derivatives)
  - Futu/Moomoo (HK/US/CN equities)
  - IBKR (global multi-asset)
  - Tiger Securities (US/HK equities)

Features:
  - Connection lifecycle (connect/disconnect/reconnect)
  - Health monitoring
  - Configuration persistence
  - Per-provider status tracking
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).parent.parent / "connections.json"


class ConnectionStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    DISABLED = "disabled"


class ProviderType(str, Enum):
    EQUITY = "equity"
    CRYPTO = "crypto"
    MULTI = "multi-asset"


@dataclass
class ConnectionConfig:
    """Configuration for a single broker connection."""
    provider: str
    enabled: bool = False
    paper_trading: bool = True
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectionStatusInfo:
    """Runtime status for a broker connection."""
    provider: str
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    message: str = ""
    last_check: float = 0.0
    last_connected: float = 0.0
    error_count: int = 0


class ConnectionManager:
    """Manages all broker/exchange connections."""

    # Provider metadata
    PROVIDER_META = {
        "alpaca": {
            "name": "Alpaca",
            "type": ProviderType.EQUITY,
            "icon": "📈",
            "description": "US equities, paper & live trading",
            "fields": [
                {"key": "api_key", "label": "API Key ID", "type": "text", "required": True},
                {"key": "api_secret", "label": "API Secret", "type": "password", "required": True},
                {"key": "base_url", "label": "Base URL", "type": "text",
                 "default": "https://paper-api.alpaca.markets"},
            ],
            "default_paper": True,
        },
        "binance": {
            "name": "Binance",
            "type": ProviderType.CRYPTO,
            "icon": "🟡",
            "description": "Crypto spot + futures, testnet support",
            "fields": [
                {"key": "api_key", "label": "API Key", "type": "text", "required": True},
                {"key": "api_secret", "label": "API Secret", "type": "password", "required": True},
                {"key": "default_type", "label": "Market Type", "type": "select",
                 "options": ["spot", "future"], "default": "spot"},
            ],
            "default_paper": True,
        },
        "bybit": {
            "name": "Bybit",
            "type": ProviderType.CRYPTO,
            "icon": "🔶",
            "description": "Crypto spot + derivatives, testnet",
            "fields": [
                {"key": "api_key", "label": "API Key", "type": "text", "required": True},
                {"key": "api_secret", "label": "API Secret", "type": "password", "required": True},
                {"key": "default_type", "label": "Market Type", "type": "select",
                 "options": ["spot", "swap", "future"], "default": "spot"},
            ],
            "default_paper": True,
        },
        "futu": {
            "name": "Futu / Moomoo",
            "type": ProviderType.MULTI,
            "icon": "🔵",
            "description": "HK/US/CN equities via OpenD gateway",
            "fields": [
                {"key": "host", "label": "OpenD Host", "type": "text", "default": "127.0.0.1"},
                {"key": "port", "label": "OpenD Port", "type": "number", "default": 11111},
                {"key": "market", "label": "Market", "type": "select",
                 "options": ["US", "HK", "CN"], "default": "US"},
            ],
            "default_paper": True,
        },
        "ibkr": {
            "name": "Interactive Brokers",
            "type": ProviderType.MULTI,
            "icon": "🏦",
            "description": "Global multi-asset via TWS/IB Gateway",
            "fields": [
                {"key": "host", "label": "TWS Host", "type": "text", "default": "127.0.0.1"},
                {"key": "port", "label": "TWS Port", "type": "number",
                 "default": 7497, "help": "7497=paper, 7496=live"},
                {"key": "client_id", "label": "Client ID", "type": "number", "default": 1},
            ],
            "default_paper": True,
        },
        "tiger": {
            "name": "Tiger Securities",
            "type": ProviderType.MULTI,
            "icon": "🐯",
            "description": "US/HK equities via Tiger Open API",
            "fields": [
                {"key": "api_key", "label": "API Key", "type": "text", "required": True},
                {"key": "account_id", "label": "Account ID", "type": "text", "required": True},
                {"key": "market", "label": "Market", "type": "select",
                 "options": ["US", "HK", "CN"], "default": "US"},
            ],
            "default_paper": True,
        },
    }

    def __init__(self):
        self._configs: Dict[str, ConnectionConfig] = {}
        self._statuses: Dict[str, ConnectionStatusInfo] = {}
        self._providers: Dict[str, Any] = {}  # Market data + execution instances
        self._load_configs()
        self._init_statuses()

    def _load_configs(self):
        """Load connection configs from file."""
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text())
                for provider, cfg in data.get("connections", {}).items():
                    self._configs[provider] = ConnectionConfig(
                        provider=provider,
                        enabled=cfg.get("enabled", False),
                        paper_trading=cfg.get("paper_trading", True),
                        config=cfg.get("config", {}),
                    )
            except Exception as e:
                logger.error(f"Failed to load connections config: {e}")

        # Ensure all providers have defaults
        for provider in self.PROVIDER_META:
            if provider not in self._configs:
                self._configs[provider] = ConnectionConfig(
                    provider=provider,
                    enabled=False,
                    paper_trading=self.PROVIDER_META[provider]["default_paper"],
                )

    def _init_statuses(self):
        """Initialize status tracking."""
        for provider in self.PROVIDER_META:
            cfg = self._configs.get(provider)
            status = ConnectionStatus.DISCONNECTED
            if cfg and not cfg.enabled:
                status = ConnectionStatus.DISABLED
            self._statuses[provider] = ConnectionStatusInfo(
                provider=provider, status=status,
            )

    def save_configs(self):
        """Persist connection configs to file."""
        data = {
            "connections": {
                p: {
                    "enabled": c.enabled,
                    "paper_trading": c.paper_trading,
                    "config": {k: v for k, v in c.config.items() if k != "api_secret"},
                }
                for p, c in self._configs.items()
            }
        }
        CONFIG_FILE.write_text(json.dumps(data, indent=2))

    def get_all_configs(self) -> List[Dict[str, Any]]:
        """Get all provider configurations."""
        result = []
        for provider, meta in self.PROVIDER_META.items():
            cfg = self._configs.get(provider, ConnectionConfig(provider=provider))
            status = self._statuses.get(provider, ConnectionStatusInfo(provider=provider))
            result.append({
                "provider": provider,
                "name": meta["name"],
                "type": meta["type"],
                "icon": meta["icon"],
                "description": meta["description"],
                "fields": meta["fields"],
                "enabled": cfg.enabled,
                "paper_trading": cfg.paper_trading,
                "config": {k: (v if k != "api_secret" else "***hidden***") for k, v in cfg.config.items()},
                "status": status.status,
                "message": status.message,
                "last_check": status.last_check,
                "last_connected": status.last_connected,
            })
        return result

    def get_config(self, provider: str) -> Optional[Dict[str, Any]]:
        """Get single provider config."""
        cfg = self._configs.get(provider)
        if not cfg:
            return None
        meta = self.PROVIDER_META.get(provider, {})
        return {
            "provider": provider,
            "name": meta.get("name", provider),
            "enabled": cfg.enabled,
            "paper_trading": cfg.paper_trading,
            "fields": meta.get("fields", []),
            "config": {k: (v if k != "api_secret" else "***hidden***") for k, v in cfg.config.items()},
        }

    def update_config(self, provider: str, enabled: bool = None,
                      paper_trading: bool = None, config: Dict[str, Any] = None):
        """Update provider configuration."""
        if provider not in self._configs:
            self._configs[provider] = ConnectionConfig(provider=provider)

        cfg = self._configs[provider]
        if enabled is not None:
            cfg.enabled = enabled
        if paper_trading is not None:
            cfg.paper_trading = paper_trading
        if config is not None:
            # Merge, don't overwrite hidden secrets
            for k, v in config.items():
                if v and v != "***hidden***":
                    cfg.config[k] = v

        self._statuses[provider].status = (
            ConnectionStatus.DISABLED if not cfg.enabled else ConnectionStatus.DISCONNECTED
        )
        self.save_configs()

    async def test_connection(self, provider: str) -> Dict[str, Any]:
        """Test connection to a provider."""
        self._statuses[provider].status = ConnectionStatus.CONNECTING
        self._statuses[provider].message = "Testing..."
        self._statuses[provider].last_check = time.time()

        try:
            cfg = self._configs.get(provider)
            if not cfg or not cfg.enabled:
                return {"success": False, "message": "Provider not enabled"}

            exec_provider = self._create_execution(provider, cfg)
            if exec_provider is None:
                self._statuses[provider].status = ConnectionStatus.ERROR
                self._statuses[provider].message = "Failed to initialize"
                self._statuses[provider].error_count += 1
                return {"success": False, "message": "Initialization failed"}

            available = await exec_provider.is_available()
            if available:
                self._statuses[provider].status = ConnectionStatus.CONNECTED
                self._statuses[provider].message = "Connected"
                self._statuses[provider].last_connected = time.time()
                self._statuses[provider].error_count = 0
                self._providers[provider] = exec_provider

                account = await exec_provider.get_account()
                return {"success": True, "message": "Connected", "account": account}
            else:
                self._statuses[provider].status = ConnectionStatus.ERROR
                self._statuses[provider].message = "Connection test failed"
                self._statuses[provider].error_count += 1
                return {"success": False, "message": "Connection test failed"}

        except Exception as e:
            self._statuses[provider].status = ConnectionStatus.ERROR
            self._statuses[provider].message = str(e)
            self._statuses[provider].error_count += 1
            logger.error(f"Connection test failed for {provider}: {e}")
            return {"success": False, "message": str(e)}

    async def test_all(self) -> Dict[str, Dict[str, Any]]:
        """Test all enabled connections."""
        results = {}
        for provider, cfg in self._configs.items():
            if cfg.enabled:
                results[provider] = await self.test_connection(provider)
            else:
                results[provider] = {"success": False, "message": "Disabled"}
        return results

    def disconnect(self, provider: str):
        """Disconnect a provider."""
        if provider in self._providers:
            del self._providers[provider]
        self._statuses[provider].status = ConnectionStatus.DISCONNECTED
        self._statuses[provider].message = "Disconnected"

    def get_active_providers(self) -> Dict[str, Any]:
        """Get all active (connected) provider instances."""
        return {k: v for k, v in self._providers.items()
                if self._statuses.get(k).status == ConnectionStatus.CONNECTED}

    def _create_execution(self, provider: str, cfg: ConnectionConfig):
        """Create an execution provider instance from config."""
        from api_layer.factory import ExecutionFactory

        try:
            provider_key = f"{provider}_{'paper' if cfg.paper_trading else 'live'}"
            if provider in ("binance", "bybit"):
                provider_key = f"{provider}_{'testnet' if cfg.paper_trading else 'live'}"

            return ExecutionFactory.create(provider_key, **cfg.config)
        except Exception as e:
            logger.error(f"Failed to create {provider} provider: {e}")
            return None

    def get_provider(self, provider: str):
        """Get an active provider instance."""
        return self._providers.get(provider)


# Global singleton
connection_manager = ConnectionManager()
