"""تنفيذ الإجراءات: متابعة، إلغاء متابعة، إعجاب، تعليق."""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, Optional, Set

from .client import ClientError, FatalClientError, IgClient
from .config import Config
from .db import Database
from .filters import TargetFilter
from .jitter import Pacer
from .models import ActionType, TargetState
from .quota import QuotaExceeded, QuotaManager
from .spintax import expand

log = logging.getLogger("instarashq.actions")


class ActionRunner:
    """ينفّذ إجراءً واحداً على هدف واحد مع كل الضوابط."""

    def __init__(
        self,
        client: IgClient,
        db: Database,
        cfg: Config,
        quota: QuotaManager,
        pacer: Pacer,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.client = client
        self.db = db
        self.cfg = cfg
        self.quota = quota
        self.pacer = pacer
        self.rng = rng or random.Random()
        self._self_pk: Optional[str] = None
        self._followers_cache: Set[str] = set()
        self._unfollows_since_refresh = 0

    def _self(self) -> str:
        if not self._self_pk:
            self._self_pk = self.client.self_pk()
        return self._self_pk

    # ---------- متابعة ----------
    def follow_next(self) -> bool:
        """يحاول متابعة هدف واحد. يُرجع True عند نجاح المتابعة."""
        if not self.quota.can(ActionType.FOLLOW.value):
            return False
        if self.quota.following_headroom() <= 0:
            log.info("بلغت سقف المتابَعين الكلي (%d)", self.cfg.limits.max_following_total)
            return False

        row = self.db.next_target()
        if row is None:
            return False

        pk = row["pk"]
        username = row["username"]

        try:
            user = self.client.user_info(pk)
        except FatalClientError:
            raise
        except ClientError as exc:
            self.db.bump_attempts(pk, f"enrich:{exc}")
            if int(row["attempts"]) >= 2:
                self.db.set_state(pk, TargetState.FAILED.value, "enrich_failed")
            self.db.record_action(pk, "enrich", False, str(exc))
            return False

        self.db.update_enrichment(
            pk,
            {
                "full_name": getattr(user, "full_name", ""),
                "follower_count": getattr(user, "follower_count", 0),
                "following_count": getattr(user, "following_count", 0),
                "media_count": getattr(user, "media_count", 0),
                "is_private": getattr(user