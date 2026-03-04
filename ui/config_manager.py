"""Configuration manager for surgical .env file updates.

This module handles all .env file read/write operations, preserving comments,
blank lines, and any keys not in the Pydantic model.
"""

import os
from pathlib import Path
from typing import Dict, Optional, Any
from ui.config import Settings, mask_api_key


# Path to .env file (relative to project root)
ENV_FILE_PATH = Path(__file__).parent.parent / ".env"


def get_config() -> Settings:
    """Load configuration from environment variables.
    
    Returns:
        Settings object with current configuration
    """
    return Settings()


def get_masked_config() -> Dict[str, Any]:
    """Get configuration with API keys masked for safe display.
    
    Returns:
        Dictionary with masked API keys
    """
    config = get_config()
    config_dict = config.to_dict()
    
    # Mask sensitive fields
    api_key_fields = [
        "CRYPTO_COM_API_KEY",
        "CRYPTO_COM_API_SECRET", 
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY"
    ]
    
    masked = {}
    for key, value in config_dict.items():
        if key in api_key_fields:
            masked[key] = mask_api_key(value)
        else:
            masked[key] = value
    
    return masked


def reveal_api_key(field: str) -> Optional[str]:
    """Reveal a specific API key value.
    
    Args:
        field: The field name to reveal (e.g., 'ANTHROPIC_API_KEY')
        
    Returns:
        The raw API key value, or None if not found/not an API key
    """
    allowed_fields = [
        "CRYPTO_COM_API_KEY",
        "CRYPTO_COM_API_SECRET",
        "OPENROUTER_API_KEY", 
        "ANTHROPIC_API_KEY"
    ]
    
    if field not in allowed_fields:
        return None
    
    config = get_config()
    return getattr(config, field, None)


def update_env_file(updates: Dict[str, Any]) -> None:
    """Update .env file surgically, preserving comments and unrelated keys.
    
    Args:
        updates: Dictionary of key-value pairs to update
    """
    if not ENV_FILE_PATH.exists():
        # Create .env file if it doesn't exist
        with open(ENV_FILE_PATH, "w") as f:
            for key, value in updates.items():
                f.write(f"{key}={value}\n")
        return
    
    # Read existing .env file
    with open(ENV_FILE_PATH, "r") as f:
        lines = f.readlines()
    
    # Track which keys we've updated
    updated_keys = set()
    new_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # Preserve empty lines and comments
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        
        # Parse key=value
        if "=" in stripped:
            key = stripped.split("=", 1)[0]
            
            if key in updates:
                # Update this line with new value
                value = updates[key]
                if value is not None and value != "":
                    new_lines.append(f"{key}={value}\n")
                updated_keys.add(key)
            else:
                # Preserve existing line
                new_lines.append(line)
    
    # Add any new keys that weren't in the original file
    for key, value in updates.items():
        if key not in updated_keys and value is not None and value != "":
            new_lines.append(f"{key}={value}\n")
    
    # Write back to .env
    with open(ENV_FILE_PATH, "w") as f:
        f.writelines(new_lines)


def save_config(updates: Dict[str, Any]) -> None:
    """Save configuration updates to .env file.
    
    Args:
        updates: Dictionary of configuration values to save
    """
    # Validate using Pydantic before saving
    # First get existing config
    existing = get_config()
    existing_dict = existing.to_dict()
    
    # Merge with updates (updates take precedence)
    merged = {**existing_dict, **updates}
    
    # Create new Settings to validate
    try:
        validated = Settings(**merged)
    except Exception as e:
        # Re-raise validation errors with field names
        raise ValueError(str(e))
    
    # Extract only the fields that are in our model
    model_fields = set(Settings.model_fields.keys())
    to_save = {k: v for k, v in updates.items() if k in model_fields}
    
    # Update .env file
    update_env_file(to_save)
