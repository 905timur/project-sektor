"""
SurrealDB Shadow Layer - Fire-and-forget dual-write to SurrealDB.

This module provides a thin synchronous wrapper around the SurrealDB Python SDK.
All writes are performed in daemon background threads to never block the main bot loop.
SurrealDB is treated as a shadow/secondary store - SQLite and JSON files remain
the primary source of truth for all reads.
"""

import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import surrealdb, but gracefully handle if not installed
try:
    import surrealdb
    from surrealdb import AsyncSurreal
    SURREALDB_AVAILABLE = True
except ImportError:
    SURREALDB_AVAILABLE = False
    logger.warning("[SurrealDB shadow] surrealdb package not installed - shadow layer disabled")


class SurrealClient:
    """
    Thin synchronous wrapper around SurrealDB for shadow writes.
    
    All public methods execute in daemon background threads and return immediately.
    If SurrealDB is unavailable, all methods silently no-op.
    """
    
    def __init__(self):
        self.enabled = False
        
        # Try to import and check availability
        if not SURREALDB_AVAILABLE:
            logger.info("[SurrealDB shadow] SurrealDB disabled - package not installed")
            return
        
        # Get URL from config if available, otherwise use default
        try:
            from config import Config
            url = getattr(Config, 'SURREALDB_URL', None) or "surrealkv://data/surreal.db"
        except Exception:
            url = "surrealkv://data/surreal.db"
        
        # Check if URL is empty or None
        if not url or url.strip() == "":
            logger.info("[SurrealDB shadow] SurrealDB disabled - URL is empty")
            return
        
        self.url = url
        self.enabled = True
        logger.info(f"[SurrealDB shadow] SurrealDB shadow layer enabled with URL: {self.url}")
    
    def _run(self, coro):
        """
        Execute an awaitable using asyncio.run() in a try/except wrapper.
        Never re-raises exceptions - logs warnings instead.
        """
        try:
            asyncio.run(coro)
        except Exception as e:
            logger.warning(f"[SurrealDB shadow] Operation failed: {e}")
    
    async def _exec(self, coro_factory):
        """
        Open a fresh AsyncSurreal connection, execute the operation, then close.
        
        Args:
            coro_factory: A callable that receives the connected db and returns an awaitable.
        """
        db = AsyncSurreal()
        try:
            await db.connect(self.url)
            await db.use("trading", "bot")
            coro = coro_factory(db)
            await coro
        finally:
            await db.close()
    
    # -------------------------------------------------------------------------
    # Public API - all fire-and-forget in daemon threads
    # -------------------------------------------------------------------------
    
    def upsert_trade(self, trade_id: str, record: dict):
        """
        UPSERT a trade record into trade:{trade_id}.
        
        Args:
            trade_id: The trade ID (will become the record ID)
            record: Dict with fields: symbol, timeframe, side, entry_price, exit_price,
                   size, pnl, pnl_percent, entry_time, exit_time, entry_reason,
                   exit_reason, stop_loss, take_profit, regime, market_context,
                   analysis_context, status
        """
        if not self.enabled:
            return
        
        async def _do_upsert(db):
            await db.upsert(f"trade:{trade_id}", record)
        
        thread = threading.Thread(target=self._run, args=(self._exec(_do_upsert),), daemon=True)
        thread.start()
    
    def merge_trade(self, trade_id: str, partial: dict):
        """
        MERGE (partial update) a trade record into trade:{trade_id}.
        
        Unlike upsert, this only updates the supplied fields without replacing the whole record.
        
        Args:
            trade_id: The trade ID
            partial: Dict with fields to merge (e.g., exit_price, pnl, status)
        """
        if not self.enabled:
            return
        
        async def _do_merge(db):
            await db.merge(f"trade:{trade_id}", partial)
        
        thread = threading.Thread(target=self._run, args=(self._exec(_do_merge),), daemon=True)
        thread.start()
    
    def upsert_screening(self, screening_id: str, record: dict):
        """
        UPSERT a screening log entry into screening:{screening_id}.
        
        Args:
            screening_id: The screening ID
            record: Dict with fields: timestamp, symbol, timeframe, model, signal,
                   confidence, reasoning, proceed, prompt_tokens, completion_tokens,
                   raw_response, escalated_to_opus
        """
        if not self.enabled:
            return
        
        async def _do_upsert(db):
            await db.upsert(f"screening:{screening_id}", record)
        
        thread = threading.Thread(target=self._run, args=(self._exec(_do_upsert),), daemon=True)
        thread.start()
    
    def update_screening_escalated(self, screening_id: str):
        """
        MERGE to mark a screening as escalated to Opus.
        
        Args:
            screening_id: The screening ID
        """
        if not self.enabled:
            return
        
        async def _do_merge(db):
            await db.merge(f"screening:{screening_id}", {"escalated_to_opus": True})
        
        thread = threading.Thread(target=self._run, args=(self._exec(_do_merge),), daemon=True)
        thread.start()
    
    def upsert_belief(self, belief_id: str, record: dict, trade_id: str = None):
        """
        UPSERT a belief record into belief:{belief_id}.
        
        If trade_id is provided and not empty, also creates a graph edge
        belief:{belief_id}->learned_from->trade:{trade_id}.
        
        Args:
            belief_id: The belief ID
            record: Dict with fields: timestamp, symbol, timeframe, outcome,
                   pnl_percent, belief, tags, confidence_in_belief
            trade_id: Optional trade ID for graph relationship
        """
        if not self.enabled:
            return
        
        async def _do_upsert(db):
            await db.upsert(f"belief:{belief_id}", record)
            
            # Create graph edge if trade_id is provided
            if trade_id and str(trade_id).strip():
                try:
                    await db.query(f"""
                        RELATE belief:{belief_id}->learned_from->trade:{trade_id}
                    """)
                except Exception as e:
                    logger.warning(f"[SurrealDB shadow] Failed to create belief->trade edge: {e}")
        
        thread = threading.Thread(target=self._run, args=(self._exec(_do_upsert),), daemon=True)
        thread.start()
    
    def upsert_state(self, state_dict: dict):
        """
        UPSERT the bot state into state:bot.
        
        Args:
            state_dict: The full bot state dict
        """
        if not self.enabled:
            return
        
        async def _do_upsert(db):
            await db.upsert("state:bot", state_dict)
        
        thread = threading.Thread(target=self._run, args=(self._exec(_do_upsert),), daemon=True)
        thread.start()
