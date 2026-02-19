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
    
    Read methods (query_*) are blocking and use _run_sync with a timeout.
    """
    
    # Read timeout for blocking read operations
    READ_TIMEOUT_SECS = 3
    
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
        
        Note: This is for fire-and-forget write operations.
        For reads, use _run_sync() instead.
        """
        try:
            asyncio.run(coro)
        except Exception as e:
            logger.warning(f"[SurrealDB shadow] Operation failed: {e}")
    
    def _run_sync(self, coro):
        """
        Runs an awaitable synchronously. Returns the result.
        Raises TimeoutError if READ_TIMEOUT_SECS exceeded.
        Raises any exception from the coroutine (caller is responsible
        for catching and triggering fallback).
        
        This is used for read operations that need to block the caller
        but must fail fast if SurrealDB is unresponsive.
        """
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=self.READ_TIMEOUT_SECS)
    
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
    
    # -------------------------------------------------------------------------
    # Public Read API - blocking calls with timeout
    # -------------------------------------------------------------------------
    
    def query_relevant_beliefs(
        self,
        symbol: str,
        timeframe: str,
        regime: str = None,
        setup_type: str = None,
        n: int = 5
    ) -> list:
        """
        Query beliefs from SurrealDB using graph traversal.
        Prioritises beliefs whose source trade had the same regime.

        Returns a list of belief dicts (same schema as beliefs.json entries).
        Raises Exception on any failure — caller must handle and fall back.
        """
        if not self.enabled:
            raise RuntimeError("SurrealDB not enabled")

        async def _query(db):
            # Primary query: beliefs linked to trades with the same regime.
            # Uses the ->learned_from->trade graph edge.
            # Ordered: regime-matched first (via sort on regime_match),
            # then by recency.
            surql = """
                SELECT
                    id,
                    symbol,
                    timeframe,
                    outcome,
                    pnl_percent,
                    belief,
                    tags,
                    confidence_in_belief,
                    timestamp,
                    ->learned_from->trade.regime AS source_regime,
                    (->learned_from->trade.regime = $regime) AS regime_match
                FROM belief
                WHERE symbol = $symbol
                  AND timeframe = $timeframe
                ORDER BY regime_match DESC, timestamp DESC
                LIMIT $n
            """
            bindings = {
                "symbol": symbol,
                "timeframe": timeframe,
                "regime": regime or "",
                "n": n
            }
            results = await db.query(surql, bindings)
            rows = results[0] if results else []

            # If fewer than n results, supplement with beliefs from any
            # symbol that share the same regime (broader search).
            if len(rows) < n and regime:
                surql_broad = """
                    SELECT
                        id, symbol, timeframe, outcome, pnl_percent,
                        belief, tags, confidence_in_belief, timestamp,
                        ->learned_from->trade.regime AS source_regime,
                        false AS regime_match
                    FROM belief
                    WHERE symbol != $symbol
                      AND ->learned_from->trade[WHERE regime = $regime] != []
                    ORDER BY timestamp DESC
                    LIMIT $remaining
                """
                existing_ids = {r.get("id") for r in rows}
                remaining = n - len(rows)
                broad_results = await db.query(surql_broad, {
                    "symbol": symbol,
                    "regime": regime,
                    "remaining": remaining
                })
                broad_rows = broad_results[0] if broad_results else []
                # Append only rows not already in primary results
                rows += [r for r in broad_rows if r.get("id") not in existing_ids]

            return rows

        raw = self._run_sync(self._exec(_query))

        # Normalise field names to match beliefs.json schema exactly
        beliefs = []
        for r in raw:
            beliefs.append({
                "id":                   str(r.get("id", "")),
                "symbol":               r.get("symbol", ""),
                "timeframe":            r.get("timeframe", ""),
                "outcome":              r.get("outcome", "UNKNOWN"),
                "pnl_percent":          r.get("pnl_percent", 0.0),
                "belief":               r.get("belief", ""),
                "tags":                 r.get("tags", []),
                "confidence_in_belief": r.get("confidence_in_belief", "MEDIUM"),
                "timestamp":            r.get("timestamp", 0),
                # Extra context injected by this query — callers may ignore
                "_source_regime":       r.get("source_regime"),
                "_regime_match":        r.get("regime_match", False),
            })
        return beliefs
    
    def query_trades_by_conditions(
        self,
        symbol: str = None,
        regime: str = None,
        timeframe: str = None,
        limit: int = 30
    ) -> list:
        """
        Query closed trades from SurrealDB filtered by conditions.
        Used by analyze_trades.py to build a condition-matched trade
        set instead of a raw recency slice.

        Matching priority (all are optional filters):
          1. Same symbol + same regime + same timeframe  (exact)
          2. Same regime + same timeframe                (regime-matched)
          3. Same regime only                           (regime only)
          4. Most recent closed trades                   (fallback within SurrealDB)

        Returns a list of trade dicts (same schema as SQLite rows).
        Raises Exception on any failure — caller must handle and fall back
        to SQLite.
        """
        if not self.enabled:
            raise RuntimeError("SurrealDB not enabled")

        async def _query(db):
            # Build WHERE clause fragments based on which filters are provided
            conditions = ["status = 'CLOSED'"]
            bindings = {"limit": limit}

            if symbol:
                conditions.append("symbol = $symbol")
                bindings["symbol"] = symbol
            if regime:
                conditions.append("regime = $regime")
                bindings["regime"] = regime
            if timeframe:
                conditions.append("timeframe = $timeframe")
                bindings["timeframe"] = timeframe

            where_clause = " AND ".join(conditions)

            surql = f"""
                SELECT
                    id, symbol, timeframe, side,
                    entry_price, exit_price, size,
                    pnl, pnl_percent,
                    entry_time, exit_time,
                    entry_reason, exit_reason,
                    stop_loss, take_profit,
                    regime, market_context, analysis_context,
                    status
                FROM trade
                WHERE {where_clause}
                ORDER BY exit_time DESC
                LIMIT $limit
            """
            results = await db.query(surql, bindings)
            rows = results[0] if results else []

            # If we have fewer than limit/2, loosen constraints and backfill
            # (drop symbol filter, keep regime + timeframe)
            if len(rows) < limit // 2 and symbol and (regime or timeframe):
                fallback_conditions = ["status = 'CLOSED'"]
                fallback_bindings = {"limit": limit, "remaining": limit - len(rows)}
                if regime:
                    fallback_conditions.append("regime = $regime")
                    fallback_bindings["regime"] = regime
                if timeframe:
                    fallback_conditions.append("timeframe = $timeframe")
                    fallback_bindings["timeframe"] = timeframe

                existing_ids = {r.get("id") for r in rows}
                fallback_where = " AND ".join(fallback_conditions)
                fallback_surql = f"""
                    SELECT id, symbol, timeframe, side,
                        entry_price, exit_price, size,
                        pnl, pnl_percent, entry_time, exit_time,
                        entry_reason, exit_reason, stop_loss, take_profit,
                        regime, market_context, analysis_context, status
                    FROM trade
                    WHERE {fallback_where}
                    ORDER BY exit_time DESC
                    LIMIT $remaining
                """
                fallback_results = await db.query(fallback_surql, fallback_bindings)
                fallback_rows = fallback_results[0] if fallback_results else []
                rows += [r for r in fallback_rows if r.get("id") not in existing_ids]

            return rows

        raw = self._run_sync(self._exec(_query))

        # Normalise to match SQLite dict schema (market_context and
        # analysis_context come back as native dicts from SurrealDB;
        # convert to JSON strings for consistency with SQLite callers)
        import json as _json
        trades = []
        for r in raw:
            mc = r.get("market_context", {})
            ac = r.get("analysis_context", {})
            trades.append({
                "id":               str(r.get("id", "")),
                "symbol":           r.get("symbol", ""),
                "timeframe":        r.get("timeframe", ""),
                "side":             r.get("side", ""),
                "entry_price":      r.get("entry_price"),
                "exit_price":       r.get("exit_price"),
                "size":             r.get("size"),
                "pnl":              r.get("pnl"),
                "pnl_percent":      r.get("pnl_percent"),
                "entry_time":       r.get("entry_time"),
                "exit_time":        r.get("exit_time"),
                "entry_reason":     r.get("entry_reason", ""),
                "exit_reason":      r.get("exit_reason", ""),
                "stop_loss":        r.get("stop_loss"),
                "take_profit":      r.get("take_profit"),
                "regime":           r.get("regime", "UNKNOWN"),
                "market_context":   _json.dumps(mc) if isinstance(mc, dict) else mc,
                "analysis_context": _json.dumps(ac) if isinstance(ac, dict) else ac,
                "status":           r.get("status", "CLOSED"),
            })
        return trades
