"""FastAPI application for the crypto trading bot UI.

This module provides:
- WebSocket endpoint /ws/status for live dashboard updates
- HTML templates using Jinja2
- HTMX-powered form submissions
"""

import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ui.config import Settings
from ui.config_manager import get_config, get_masked_config


# Create FastAPI app
app = FastAPI(title="Imbalance Bot UI", description="Web UI for crypto trading agent")

# Setup templates
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# Include routers
from ui.routers import settings, agent

app.include_router(settings.router)
app.include_router(agent.router)

# Mount static files (empty for now, but ready for future use)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


# ============================================================================
# WebSocket for live dashboard updates
# ============================================================================

class ConnectionManager:
    """Manages WebSocket connections for live updates."""
    
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    
    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)
    
    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                # Remove broken connections
                self.active_connections.remove(connection)


manager = ConnectionManager()


# Global state (placeholder - would connect to actual trading bot)
agent_state = {
    "running": False,
    "positions": [],
    "screening_results": []
}


@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    """WebSocket endpoint for live dashboard updates.
    
    Emits a JSON heartbeat every 3 seconds with:
    {
        "agent_running": bool,
        "positions": [],
        "screening_results": [],
        "timestamp": ISO8601
    }
    """
    await manager.connect(websocket)
    try:
        while True:
            # Get current state
            # In a real implementation, this would query the trading bot
            status = {
                "agent_running": agent_state["running"],
                "positions": agent_state["positions"],
                "screening_results": agent_state["screening_results"],
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
            await websocket.send_json(status)
            
            # Wait 3 seconds before next heartbeat
            await asyncio.sleep(3)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ============================================================================
# Page routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard page showing agent status and positions."""
    config = get_config()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "paper_trading": config.PAPER_TRADING,
            "agent_running": agent_state["running"]
        }
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings page for configuration."""
    config = get_config()
    masked = get_masked_config()
    
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "config": masked,
            "paper_trading": config.PAPER_TRADING,
            "errors": {}  # Empty errors on initial load
        }
    )


# ============================================================================
# Utility endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


# ============================================================================
# Agent state management (for WebSocket)
# ============================================================================

def update_agent_state(running: bool = None, positions: list = None, screening_results: list = None):
    """Update the global agent state (for testing/demo purposes)."""
    if running is not None:
        agent_state["running"] = running
    if positions is not None:
        agent_state["positions"] = positions
    if screening_results is not None:
        agent_state["screening_results"] = screening_results


# Run with: uvicorn ui.main:app --reload --port 8000
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
