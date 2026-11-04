"""تصفية الأهداف وفق معايير الجودة."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .config import FiltersConfig


@dataclass
class FilterVerdict:
    ok: bool
    reason: Optional[str] = None


class TargetFilter:
    """يطبّق معايير الاستهداف على بيانات مستخدم مُثراة."""

    def __init__(self, cfg: FiltersConfig, self_pk: Optional[str] = None) -> None:
        self.cfg = cfg
        self.self_pk = self_pk

    def evaluate(self, user: Any) -> FilterVerdict:
        pk = str(getattr(user, "pk", "") or "")
        if self.self_pk and pk == self.self_pk:
            return FilterVerdict(False, "self")

        followers = int(getattr(user, "follower_count", 0) or 0)
        following = int(getattr(user, "following_count", 0) or 0)
        media = int(getattr(user, "media_count", 0) or 0)

        if followers < self.cfg.min_followers:
            return FilterVerdict(False, f"followers<{self.cfg.min_followers}")
        if followers > self.cfg.max_followers:
            return FilterVerdict(False, f"followers>{self.cfg.max_followers}")
        if self.cfg.skip_private and bool(getattr(user, "is_private", False)):
            return FilterVerdict(False, "private")
        if self.cfg.skip_verified and bool(getattr(user, "is_verified", False)):
            return FilterVerdict(False, "verified")
        if self.cfg.skip_business and bool(getattr(user, "is_business", False)):
            return FilterVerdict(False, "business")
        if self.cfg.require_profile_pic and not getattr(user, "profile_pic_url", None):
            return FilterVerdict(False, "no_profile_pic")
        if media < self.cfg.min_posts:
            return FilterVerdict(False, f"posts<{self.cfg.min_posts}")
        if following > 0 and followers > 0:
            ratio = following / followers
            if ratio > self.cfg.max_following_ratio:
                return FilterVerdict(False, f"ratio>{self.cfg.max_following_ratio}")
        if self.cfg.languages:
            bio = (getattr(user, "biography", "") or "").lower()
            if bio and not any(lang in bio for lang in self.cfg.languages):
                return FilterVerdict(False, "language")
        return FilterVerdict(True)
