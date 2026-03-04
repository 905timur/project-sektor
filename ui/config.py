"""Pydantic Settings model for the crypto trading bot UI."""

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Settings model that loads from environment variables."""
    
    # Exchange API Keys
    CRYPTO_COM_API_KEY: Optional[str] = Field(default="", description="Crypto.com API Key")
    CRYPTO_COM_API_SECRET: Optional[str] = Field(default="", description="Crypto.com API Secret")
    
    # AI Providers
    OPENROUTER_API_KEY: Optional[str] = Field(default="", description="OpenRouter API Key (for DeepSeek)")
    ANTHROPIC_API_KEY: Optional[str] = Field(default="", description="Anthropic API Key (for Claude)")
    
    # Screening Parameters
    SCREENING_MIN_VOLUME_USD: float = Field(default=1_000_000, description="Minimum 24h volume in USD")
    SCREENING_MIN_IMBALANCE_PCT: float = Field(default=0.05, description="Minimum imbalance percentage")
    SCREENING_INTERVAL_SECONDS: int = Field(default=60, description="Screening interval in seconds")
    
    # AI Models
    DEEPSEEK_MODEL: str = Field(default="deepseek/deepseek-chat", description="DeepSeek model for screening")
    OPUS_MODEL: str = Field(default="claude-opus-4-5", description="Claude Opus model for analysis")
    
    # Risk Management
    MAX_POSITION_SIZE_USD: float = Field(default=500, description="Maximum position size in USD")
    MAX_OPEN_POSITIONS: int = Field(default=3, description="Maximum number of open positions")
    PAPER_TRADING: bool = Field(default=True, description="Paper trading mode")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Allow extra fields from .env
    
    def to_dict(self) -> dict:
        """Convert settings to dictionary, excluding empty values."""
        return {k: v for k, v in self.model_dump().items() if v is not None and v != ""}


def mask_api_key(key: Optional[str], visible_chars: int = 4) -> str:
    """Mask an API key, showing only the last N characters.
    
    Args:
        key: The API key to mask
        visible_chars: Number of characters to show at the end
        
    Returns:
        Masked string like "sk-••••••1a2b"
    """
    if not key or key == "":
        return ""
    
    if len(key) <= visible_chars:
        return "•" * len(key)
    
    masked_length = len(key) - visible_chars
    return "•" * masked_length + key[-visible_chars:]
