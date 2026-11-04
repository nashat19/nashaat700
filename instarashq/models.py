"""نماذج البيانات والأنواع المشتركة."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


def utcnow() -> str:
    """طابع زمني ISO-8601 بتوقيت UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ActionType(str, Enum):
    FOLLOW = "follow"
    UNFOLLOW = "unfollow"
    LIKE = "like"
    COMMENT = "comment"
    HARVEST = "harvest"
    ENRICH = "enrich"


class TargetState(str, Enum):
    NEW = "new"
    QUEUED = "queued"
    FOLLOWED = "followed"
    SKIPPED = "skipped"
    FAILED = "failed"
    UNFOLLOWED = "unfollowed"
    WHITELISTED = "whitelisted"


class HarvestSource(str, Enum):
    SEED_FOLLOWERS = "seed_followers"
    SEED_FOLLOWING = "seed_following"
    HASHTAG = "hashtag"
    LOCATION = "location"
    POST_LIKERS = "post_likers"
    POST_COMMENTERS = "post_commenters"


@dataclass
class Target:
    pk: str
    username: str
    source: str
    full_name: str = ""
    follower_count: Optional[int] = None
    following_count: Optional[int] = None
    media_count: Optional[int] = None
    is_private: bool = False
    is_verified: bool = False
    is_business: bool = False
    has_profile_pic: bool = False
    language: Optional[str] = None
    score: float = 0.0
    state: str = TargetState.NEW.value
    harvested_at: str = field(default_factory=utcnow)
    enriched_at: Optional[str] = None
    followed_at: Optional[str] = None
    unfollowed_at: Optional[str] = None
    last_action_at: Optional[str] = None
    attempts: int = 0
    note: Optional[str] = None

    def to_row(self) -> Dict[str, Any]:
        return {
            "pk": self.pk,
            "username": self.username,
            "full_name": self.full_name,
            "source": self.source,
            "follower_count": self.follower_count,
            "following_count": self.following_count,
            "media_count": self.media_count,
            "is_private": int(self.is_private),
            "is_verified": int(self.is_verified),
            "is_business": int(self.is_business),
            "has_profile_pic": int(self.has_profile_pic),
            "language": self.language,
            "score": self.score,
            "state": self.state,
            "harvested_at": self.harvested_at,
            "enriched_at": self.enriched_at,
            "followed_at": self.followed_at,
            "unfollowed_at": self.unfollowed_at,
            "last_action_at": self.last_action_at,
            "attempts": self.attempts,
            "note": self.note,
        }


@dataclass
class ActionRecord:
    pk: Optional[str]
    action: str
    ok: bool
    error: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    ts: str = field(default_factory=utcnow)


@dataclass
class QuotaSnapshot:
    day: str
    action: str
    used: int
    limit: int
    remaining: int
