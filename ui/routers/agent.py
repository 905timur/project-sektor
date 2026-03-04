"""Agent API routes for start/stop control.

Provides:
- POST /agent/start - Start the trading agent
- POST /agent/stop - Stop the trading agent

These are placeholder endpoints that return {"status": "not_implemented"}.
They can be wired up to actual agent control later.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict, Any


router = APIRouter(prefix="/agent", tags=["agent"])


# Import the global agent state from main
# This is a workaround to share state between routers
def get_agent_state():
    """Get agent state dict - will be imported from main.py"""
    from ui.main import agent_state
    return agent_state


@router.post("/start")
async def start_agent(request: Request) -> JSONResponse:
    """Start the trading agent.
    
    Returns:
        JSON response with status (placeholder - not implemented)
    """
    # TODO: Wire up to actual trading bot
    # For now, just return a placeholder response
    
    return JSONResponse({
        "status": "not_implemented",
        "message": "Agent start not yet implemented"
    })


@router.post("/stop")
async def stop_agent(request: Request) -> JSONResponse:
    """Stop the trading agent.
    
    Returns:
        JSON response with status (placeholder - not implemented)
    """
    # TODO: Wire up to actual trading bot
    # For now, just return a placeholder response
    
    return JSONResponse({
        "status": "not_implemented",
        "message": "Agent stop not yet implemented"
    })


@router.get("/status")
async def agent_status() -> JSONResponse:
    """Get current agent status.
    
    Returns:
        JSON with agent running state
    """
    state = get_agent_state()
    
    return JSONResponse({
        "running": state["running"],
        "positions_count": len(state["positions"]),
        "screening_results_count": len(state["screening_results"])
    })
