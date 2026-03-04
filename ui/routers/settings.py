"""Settings API routes for configuration CRUD.

Provides:
- GET /settings - Get masked configuration
- POST /settings - Save configuration with validation
- GET /settings/reveal - Reveal a specific API key
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict, Any

from ui.config import Settings
from ui.config_manager import (
    get_config,
    get_masked_config,
    reveal_api_key,
    save_config
)


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_class=HTMLResponse)
async def get_settings(request: Request):
    """Get settings page with masked API keys."""
    from fastapi.templating import Jinja2Templates
    from pathlib import Path
    
    templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
    config = get_config()
    masked = get_masked_config()
    
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "config": masked,
            "paper_trading": config.PAPER_TRADING,
            "errors": {}
        }
    )


@router.post("")
async def save_settings(request: Request) -> HTMLResponse:
    """Save settings with Pydantic validation.
    
    Returns HTMX partial with:
    - Success: toast notification
    - Failure: form with inline field errors
    """
    from fastapi.templating import Jinja2Templates
    from pathlib import Path
    
    templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
    
    # Parse form data
    form_data = await request.form()
    updates = dict(form_data)
    
    # Convert string booleans to actual booleans
    if "PAPER_TRADING" in updates:
        updates["PAPER_TRADING"] = updates["PAPER_TRADING"].lower() in ("true", "1", "yes")
    
    # Convert numeric fields
    numeric_fields = [
        "SCREENING_MIN_VOLUME_USD",
        "SCREENING_MIN_IMBALANCE_PCT", 
        "SCREENING_INTERVAL_SECONDS",
        "MAX_POSITION_SIZE_USD",
        "MAX_OPEN_POSITIONS"
    ]
    
    for field in numeric_fields:
        if field in updates and updates[field]:
            try:
                updates[field] = float(updates[field]) if "." in updates[field] else int(updates[field])
            except ValueError:
                pass  # Keep as string if can't parse
    
    # Get existing config
    existing = get_config()
    existing_dict = existing.to_dict()
    
    # Merge with updates
    merged = {**existing_dict, **updates}
    
    # Validate using Pydantic
    try:
        validated = Settings(**merged)
    except Exception as e:
        # Parse validation errors
        error_str = str(e)
        errors = {}
        
        # Try to extract field-specific errors
        for line in error_str.split("\n"):
            if "Field" in line or "Input" in line:
                # Try to extract field name
                parts = line.split(" ")
                for i, part in enumerate(parts):
                    if part == "Field" and i + 1 < len(parts):
                        field_name = parts[i + 1].strip()
                        errors[field_name] = line
                        break
        
        # If we couldn't parse individual fields, show general error
        if not errors:
            errors["_general"] = error_str
        
        # Re-render form with errors
        masked = get_masked_config()
        # Include submitted values so form doesn't lose data
        for key, value in updates.items():
            if key in masked:
                masked[key] = value
        
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "config": masked,
                "paper_trading": existing.PAPER_TRADING,
                "errors": errors
            },
            status_code=400
        )
    
    # Save to .env
    try:
        save_config(updates)
    except Exception as e:
        masked = get_masked_config()
        for key, value in updates.items():
            if key in masked:
                masked[key] = value
        
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "config": masked,
                "paper_trading": existing.PAPER_TRADING,
                "errors": {"_general": f"Failed to save: {str(e)}"}
            },
            status_code=400
        )
    
    # Success - return HTMX toast
    return HTMLResponse(
        content="""
        <div id="toast" class="fixed bottom-4 right-4 bg-green-600 text-white px-6 py-3 rounded-lg shadow-lg z-50">
            Settings saved successfully
        </div>
        <script>
            setTimeout(() => {
                document.getElementById('toast').remove();
            }, 3000);
        </script>
        """,
        headers={
            "HX-Trigger": "settings-saved"
        }
    )


@router.get("/reveal")
async def reveal_key(field: str) -> JSONResponse:
    """Reveal a specific API key value.
    
    Args:
        field: The field name to reveal (e.g., 'ANTHROPIC_API_KEY')
        
    Returns:
        JSON with the raw API key value
    """
    value = reveal_api_key(field)
    
    if value is None:
        raise HTTPException(status_code=404, detail="Field not found or not an API key")
    
    return JSONResponse({
        "field": field,
        "value": value
    })
