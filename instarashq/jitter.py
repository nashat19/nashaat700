"""تمويه بشري: تأخيرات غاوسية، فترات راحة، نافذة ساعات نشاط."""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import PacingConfig


class Pacer:
    """يحسب التأخيرات ويقرر فترات الراحة والنوافذ النشطة."""

    def __init__(self, cfg: PacingConfig, rng: Optional[random.Random] = None) -> None:
        self.cfg = cfg
        self.rng = rng or random.Random()
        try:
            self.tz = ZoneInfo(cfg.timezone)
        except ZoneInfoNotFoundError:
            self.tz = ZoneInfo("UTC")
        self._actions_since_break = 0

    # ---------- الوقت ----------
    def now_local(self) -> datetime:
        return datetime.now(self.tz)

    def in_active_window(self, moment: Optional[datetime] = None) -> bool:
        current = moment or self.now_local()
        if current.weekday() not in self.cfg.active_days:
            return False
        start, end = self.cfg.active_hours
        hour = current.hour
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end

    def seconds_until_window(self, moment: Optional[datetime] = None) -> float:
        """ثوانٍ حتى بداية النافذة النشطة التالية."""
        current = moment or self.now_local()
        if self.in_active_window(current):
            return 0.0
        start, _ = self.cfg.active_hours
        for offset in range(0, 8):
            day = (current + timedelta(days=offset)).date()
            if day.weekday() not in self.cfg.active_days:
                continue
            candidate = datetime.combine(day, datetime.min.time(), tzinfo=self.tz).replace(hour=start)
            if candidate > current:
                return (candidate - current).total_seconds()
        return 3600.0

    # ---------- التأخير ----------
    def action_delay(self) -> float:
        """تأخير غاوسي مقيّد بين min_delay و max_delay."""
        low, high = self.cfg.min_delay, self.cfg.max_delay
        mu = (low + high) / 2.0
        sigma = max((high - low) / 4.0, 0.5)
        value = self.rng.gauss(mu, sigma)
        return float(min(max(value, low), high))

    def sleep_action(self, sleeper: Callable[[float], None] = time.sleep) -> float:
        delay = self.action_delay()
        sleeper(delay)
        return delay

    def register_action(self) -> bool:
        """يزيد العدّاد ويُرجع True إن حان وقت راحة."""
        self._actions_since_break += 1
        if self.cfg.break_every and self._actions_since_break >= self.cfg.break_every:
            self._actions_since_break = 0
            return True
        return False

    def break_duration(self) -> float:
        return float(self.rng.uniform(self.cfg.break_min, self.cfg.break_max))

    def sleep_break(self, sleeper: Callable[[float], None] = time.sleep) -> float:
        duration = self.break_duration()
        sleeper(duration)
        return duration

    def reset(self) -> None:
        self._actions_since_break = 0
