"""
Agents API — FastAPI routes and WebSocket endpoints V7.0
=========================================================
Fixed:
  - Added missing json import
  - Enhanced error handling
  - Better WebSocket management
  - V7.0: WebSocket message size limit (1MB)
  - V7.0: Input validation and sanitization
"""
from __future__ import annotations
import asyncio
import json  # FIXED: Was missing!
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse

from .engine import get_trading_engine, RealTimeTradingEngine
from .base import AgentOrchestrator
from .ai_agent import get_ai_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

_engine: Optional[RealTimeTradingEngine] = None
_connection_manager = None


class ConnectionManager:
    """Manage WebSocket connections."""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"WebSocket client connected: {client_id}")

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        logger.info(f"WebSocket client disconnected: {client_id}")

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        disconnected = []
        for client_id, connection in self.active_connections.items():
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Broadcast to {client_id} failed: {e}")
                disconnected.append(client_id)

        for client_id in disconnected:
            self.disconnect(client_id)

    async def send_personal(self, client_id: str, message: dict):
        """Send message to specific client."""
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_json(message)
            except Exception as e:
                logger.error(f"Send to {client_id} failed: {e}")
                self.disconnect(client_id)


manager = ConnectionManager()

# V7.0: WebSocket message size limit (1MB)
MAX_WS_MESSAGE_SIZE = 1_048_576  # 1MB in bytes


def get_engine() -> RealTimeTradingEngine:
    """Get or create trading engine."""
    global _engine
    if _engine is None:
        _engine = get_trading_engine()
    return _engine


@router.get("/status")
async def get_agents_status():
    """Get status of all agents and the trading engine."""
    engine = get_engine()
    return {
        "engine": engine.get_engine_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/list")
async def list_agents():
    """List all registered agents."""
    engine = get_engine()
    agents = engine.orchestrator.get_all_states()
    return {"agents": agents}


@router.get("/{agent_id}/state")
async def get_agent_state(agent_id: str):
    """Get state of a specific agent."""
    engine = get_engine()

    for agent in engine.orchestrator.agents.values():
        if agent.agent_id == agent_id:
            return agent.get_state()

    raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


@router.post("/start")
async def start_engine(auto_trade: bool = False):
    """Start the trading engine."""
    engine = get_engine()
    engine.start(auto_trade=auto_trade)
    asyncio.create_task(engine.run_loop())

    return {
        "status": "started",
        "auto_trade": auto_trade,
        "message": "Trading engine started",
    }


@router.post("/stop")
async def stop_engine():
    """Stop the trading engine."""
    engine = get_engine()
    engine.stop()
    return {"status": "stopped", "message": "Trading engine stopped"}


@router.post("/toggle-auto-trade")
async def toggle_auto_trade(enable: bool):
    """Toggle auto-trading mode."""
    engine = get_engine()
    engine._auto_trade = enable
    return {
        "status": "updated",
        "auto_trade": enable,
        "message": f"Auto-trading {'enabled' if enable else 'disabled'}",
    }


@router.get("/decisions")
async def get_decisions(limit: int = 10):
    """Get recent trading decisions."""
    engine = get_engine()
    return {"decisions": engine.get_decisions(limit)}


@router.get("/trades/active")
async def get_active_trades():
    """Get active trades."""
    engine = get_engine()
    return {"trades": engine.get_active_trades()}


@router.get("/consensus/{pair}")
async def get_consensus(pair: str):
    """Get agent consensus for a pair."""
    engine = get_engine()
    pair = pair.replace("_", "/")
    signal, confidence, reasoning = engine.orchestrator.get_consensus(pair)

    return {
        "pair": pair,
        "signal": signal.value,
        "confidence": confidence,
        "reasoning": reasoning,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    client_id = f"client_{datetime.now().timestamp()}"
    await manager.connect(websocket, client_id)

    engine = get_engine()
    engine.add_websocket_client(websocket)

    try:
        await manager.send_personal(client_id, {
            "type": "init",
            "data": engine.get_engine_status(),
        })

        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # V7.0: WebSocket message size validation
                if len(data.encode('utf-8')) > MAX_WS_MESSAGE_SIZE:
                    await manager.send_personal(client_id, {
                        "type": "error",
                        "data": {"message": f"Message size exceeds {MAX_WS_MESSAGE_SIZE} bytes limit"},
                    })
                    continue
                message = json.loads(data)  # Now works with json imported!
                if message.get("type") == "ping":
                    await manager.send_personal(client_id, {"type": "pong"})
            except asyncio.TimeoutError:
                await manager.send_personal(client_id, {"type": "heartbeat"})
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {client_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        manager.disconnect(client_id)
        engine.remove_websocket_client(websocket)


@router.post("/agent/{agent_id}/configure")
async def configure_agent(agent_id: str, config: Dict[str, Any]):
    """Update agent configuration."""
    engine = get_engine()

    if agent_id not in engine.orchestrator.agents:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    agent = engine.orchestrator.agents[agent_id]
    agent.config.update(config)

    return {
        "status": "updated",
        "agent_id": agent_id,
        "config": agent.config,
    }


@router.get("/metrics")
async def get_agent_metrics():
    """Get performance metrics for all agents."""
    engine = get_engine()

    metrics = []
    for agent in engine.orchestrator.agents.values():
        state = agent.get_state()
        metrics.append({
            "agent_id": state["agent_id"],
            "role": state["role"],
            "decisions_made": state["decisions_made"],
            "win_rate": state["win_rate"],
            "total_pnl": state["total_pnl"],
            "successful_trades": state["successful_trades"],
            "failed_trades": state["failed_trades"],
        })

    return {
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
