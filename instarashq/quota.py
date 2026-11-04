"""محرك الحصص: سقف يومي/ساعي مع تسخين تدريجي للحسابات الجديدة."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict, Optional

from .config import LimitsConfig
from .db import Database
from .models import ActionType

TRACKED = (
    ActionType.FOLLOW.value,
    ActionType.UNFOLLOW.value,
    ActionType.LIKE.value,
    ActionType.COMMENT.value,
    ActionType.HARVEST.value,
    ActionType.ENRICH.value,
)


class QuotaExceeded(Exception):
    """تم بلوغ سقف الحصة."""


class QuotaManager:
    """يفرض السقوف ويطبّق معامل التسخين."""

    def __init__(self, db: Database, limits: LimitsConfig, started_at: Optional[str] = None) -> None:
        self.db = db
        self.limits = limits
        self.started_at = started_at or db.kv_get("account_started_at")
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.kv_set("account_started_at", self.started_at)

    # ---------- التسخين ----------
    def warmup_factor(self) -> float:
        days = self.limits.warmup_days
        if days <= 0:
            return 1.0
        try:
            start = datetime.fromisoformat(self.started_at)
        except ValueError:
            return 1.0
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - start).total_seconds() / 86400.0
        if elapsed >= days:
            return 1.0
        return min(1.0, 0.25 + 0.75 * (elapsed / days))

    def effective_limit(self, action: str, window: str) -> int:
        base = self.limits.daily(action) if window == "daily" else self.limits.hourly(action)
        return max(1, int(round(base * self.warmup_factor())))

    # ---------- الاستهلاك ----------
    @staticmethod
    def _day() -> str:
        return date.today().isoformat()

    def used_today(self, action: str) -> int:
        return self.db.quota_used(self._day(), action)

    def used_this_hour(self, action: str) -> int:
        since = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        return self.db.action_counts_since(action, since)

    def remaining(self, action: str) -> int:
        daily_left = self.effective_limit(action, "daily") - self.used_today(action)
        hourly_left = self.effective_limit(action, "hourly") - self.used_this_hour(action)
        return max(0, min(daily_left, hourly_left))

    def can(self, action: str) -> bool:
        return self.remaining(action) > 0

    def consume(self, action: str, amount: int = 1) -> None:
        if self.remaining(action) < amount:
            raise QuotaExceeded(f"الحصة منتهية للإجراء {action}")
        self.db.quota_bump(self._day(), action, amount)

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for action in TRACKED:
            out[action] = {
                "used_today": self.used_today(action),
                "daily_limit": self.effective_limit(action, "daily"),
                "remaining": self.remaining(action),
            }
        return out

    def following_headroom(self) -> int:
        return max(0, self.limits.max_following_total - self.db.following_count())
