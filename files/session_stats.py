from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass
class SessionStats:
    session_name: str = ""
    started_at: str = ""          # Human-readable ET datetime string

    # Funnel counters
    zones_entered: int = 0        # Retracement confirmed → triggered analysis path
    rejected_candle: int = 0      # Failed rejection candle gate
    rejected_volume: int = 0      # Failed volume gate
    rejected_deepseek: int = 0    # DeepSeek said no
    rejected_opus: int = 0        # Opus said no
    trades_executed: int = 0      # execute_trade() called

    def reset(self, new_session_name: str):
        """Reset all counters for a new session."""
        et = ZoneInfo("America/New_York")
        self.session_name = new_session_name
        self.started_at = datetime.now(et).strftime("%Y-%m-%d %H:%M ET")
        self.zones_entered = 0
        self.rejected_candle = 0
        self.rejected_volume = 0
        self.rejected_deepseek = 0
        self.rejected_opus = 0
        self.trades_executed = 0

    @property
    def total_rejected(self) -> int:
        return (self.rejected_candle + self.rejected_volume +
                self.rejected_deepseek + self.rejected_opus)

    def format_telegram(self) -> str:
        """Format a Telegram-ready session summary string."""
        total_seen = self.zones_entered
        conversion = (
            f"{self.trades_executed}/{total_seen}"
            if total_seen > 0 else "0/0"
        )
        pct = (
            f"{self.trades_executed / total_seen * 100:.0f}%"
            if total_seen > 0 else "—"
        )

        return (
            f"📊 *Session Report: {self.session_name}*\n"
            f"Started: {self.started_at}\n"
            f"───────────────────\n"
            f"🔍 Zones entered:       {self.zones_entered}\n"
            f"❌ Rejected (candle):   {self.rejected_candle}\n"
            f"❌ Rejected (volume):   {self.rejected_volume}\n"
            f"⏭️ Rejected (DeepSeek): {self.rejected_deepseek}\n"
            f"🧠 Rejected (Opus):     {self.rejected_opus}\n"
            f"✅ Trades executed:     {self.trades_executed}\n"
            f"───────────────────\n"
            f"Conversion: {conversion} ({pct})"
        )
