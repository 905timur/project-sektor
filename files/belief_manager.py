"""
BeliefManager - Manages self-reflection beliefs for Opus

Beliefs are stored in beliefs.json and represent lessons learned from closed trades.
Each belief is a first-person reflection written by Opus about what it got right or wrong.
"""
import json
import os
import time
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from config import Config
from surreal_client import SurrealClient

logger = logging.getLogger(__name__)


class BeliefManager:
    """Manages reading and writing beliefs to beliefs.json"""
    
    def __init__(self):
        self.file_path = Config.BELIEFS_FILE
        self._ensure_file()
        self.surreal = SurrealClient()
    
    def _ensure_file(self):
        """Create the file and data dir if they don't exist"""
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        if not os.path.exists(self.file_path):
            with open(self.file_path, 'w') as f:
                json.dump({"beliefs": []}, f)
    
    def load_beliefs(self) -> list:
        """Read and return the beliefs list. Return [] on any error."""
        try:
            with open(self.file_path, 'r') as f:
                data = json.load(f)
                return data.get("beliefs", [])
        except Exception as e:
            logger.warning(f"Failed to load beliefs: {e}")
            return []
    
    def add_belief(self, belief_dict: dict):
        """
        Append new belief to beliefs.json.
        belief_dict must contain all fields from the schema:
        - id, timestamp, symbol, timeframe, outcome, pnl_percent, belief, tags
        Assign id = f"belief_{int(time.time())}" if not present.
        
        Note: We never prune beliefs - they accumulate forever.
        """
        try:
            beliefs = self.load_beliefs()
            
            # Assign ID if not present
            if "id" not in belief_dict:
                belief_dict["id"] = f"belief_{int(time.time())}"
            
            # Ensure timestamp is set
            if "timestamp" not in belief_dict:
                belief_dict["timestamp"] = time.time()
            
            # Append new belief
            beliefs.append(belief_dict)
            
            # Save back (no pruning - we keep all beliefs forever)
            with open(self.file_path, 'w') as f:
                json.dump({"beliefs": beliefs}, f, indent=2)
            
            # SurrealDB shadow write
            self.surreal.upsert_belief(
                belief_id=belief_dict["id"],
                record={
                    "timestamp": belief_dict.get("timestamp", time.time()),
                    "symbol": belief_dict.get("symbol"),
                    "timeframe": belief_dict.get("timeframe"),
                    "outcome": belief_dict.get("outcome"),
                    "pnl_percent": belief_dict.get("pnl_percent"),
                    "belief": belief_dict.get("belief"),
                    "tags": belief_dict.get("tags", []),
                    "confidence_in_belief": belief_dict.get("confidence_in_belief", "MEDIUM")
                },
                trade_id=belief_dict.get("trade_id")
            )
            
            logger.info(f"Belief added: {belief_dict['id']} - {belief_dict.get('symbol', 'unknown')}")
            
        except Exception as e:
            logger.error(f"Failed to add belief: {e}")
            raise
    
    def get_relevant_beliefs(self, symbol: str, timeframe: str, 
                            regime: str = None, setup_type: str = None, 
                            n: int = None) -> list:
        """
        Return the most relevant beliefs based on priority scoring.
        
        Priority scoring:
        - +3 if same symbol
        - +2 if same timeframe
        - +1 if same regime tag
        - +1 if same setup_type tag
        Tiebreak by recency (most recent first).
        
        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT')
            timeframe: Timeframe (e.g., 'daily', 'weekly')
            regime: Current market regime (optional)
            setup_type: Setup type like 'FVG', 'OB' (optional)
            n: Number of beliefs to return (default: Config.BELIEFS_INJECTED_PER_ANALYSIS)
        
        Returns:
            List of belief dicts sorted by relevance score (highest first)
        """
        if n is None:
            n = Config.BELIEFS_INJECTED_PER_ANALYSIS
        
        beliefs = self.load_beliefs()
        
        if not beliefs:
            return []
        
        # Score each belief
        scored_beliefs = []
        for belief in beliefs:
            score = 0
            
            # +3 for same symbol
            if belief.get("symbol", "").upper() == symbol.upper():
                score += 3
            
            # +2 for same timeframe
            if belief.get("timeframe", "").lower() == timeframe.lower():
                score += 2
            
            # +1 for same regime tag
            belief_tags = belief.get("tags", [])
            if regime and regime.upper() in [tag.upper() for tag in belief_tags]:
                score += 1
            
            # +1 for same setup_type tag
            if setup_type and setup_type.upper() in [tag.upper() for tag in belief_tags]:
                score += 1
            
            scored_beliefs.append((score, belief.get("timestamp", 0), belief))
        
        # Sort by score (desc), then by timestamp (desc) for tiebreak
        scored_beliefs.sort(key=lambda x: (x[0], x[1]), reverse=True)
        
        # Return top n
        return [belief for _, _, belief in scored_beliefs[:n]]
    
    def get_recent_beliefs(self, n: int = None) -> list:
        """
        Return the n most recent beliefs (newest-first).
        Kept for backward compatibility.
        """
        if n is None:
            n = Config.BELIEFS_INJECTED_PER_ANALYSIS
        
        beliefs = self.load_beliefs()
        
        if not beliefs:
            return []
        
        # Sort by timestamp descending
        sorted_beliefs = sorted(beliefs, key=lambda x: x.get("timestamp", 0), reverse=True)
        
        return sorted_beliefs[:n]
    
    def format_for_prompt(self, symbol: str = None, timeframe: str = None,
                         regime: str = None, setup_type: str = None,
                         n: int = None) -> str:
        """
        Return a formatted string of relevant beliefs suitable for injection
        into an LLM prompt.
        
        If symbol/timeframe are provided, uses relevance scoring.
        Otherwise, falls back to most recent.
        
        Format each belief as:
        ```
        [Belief #{i} | {outcome} | {symbol} {timeframe} | {date_str}]
        {belief}
        Tags: {tags}
        ---
        ```
        
        Returns empty string "" if no beliefs exist.
        
        Args:
            symbol: Trading symbol to filter by relevance
            timeframe: Timeframe to filter by relevance
            regime: Current market regime
            setup_type: Setup type (FVG, OB, etc.)
            n: Number of beliefs to include
        """
        if n is None:
            n = Config.BELIEFS_INJECTED_PER_ANALYSIS
        
        # Use relevance scoring if symbol is provided, otherwise use recent
        if symbol:
            beliefs = self.get_relevant_beliefs(symbol, timeframe, regime, setup_type, n)
        else:
            beliefs = self.get_recent_beliefs(n)
        
        if not beliefs:
            return ""
        
        formatted = []
        for i, belief in enumerate(beliefs, 1):
            timestamp = belief.get("timestamp", 0)
            date_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d") if timestamp else "unknown"
            
            outcome = belief.get("outcome", "UNKNOWN")
            symbol_belief = belief.get("symbol", "unknown")
            tf = belief.get("timeframe", "unknown")
            belief_text = belief.get("belief", "")
            tags = belief.get("tags", [])
            
            formatted.append(
                f"[Belief #{i} | {outcome} | {symbol_belief} {tf} | {date_str}]\n"
                f"{belief_text}\n"
                f"Tags: {', '.join(tags) if tags else 'none'}"
            )
        
        return "\n---\n".join(formatted)
