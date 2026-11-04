"""جمع الأهداف من مصادر: متابعو منافس، هاشتاغ، موقع، تفاعل منشور."""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from .client import IgClient
from .config import Config
from .db import Database
from .models import HarvestSource, Target, utcnow
from .quota import QuotaManager

log = logging.getLogger("instarashq.harvest")

SOURCE_WEIGHT = {
    HarvestSource.SEED_FOLLOWERS.value: 1.0,
    HarvestSource.POST_LIKERS.value: 0.9,
    HarvestSource.POST_COMMENTERS.value: 0.85,
    HarvestSource.SEED_FOLLOWING.value: 0.7,
    HarvestSource.HASHTAG.value: 0.6,
    HarvestSource.LOCATION.value: 0.5,
}


def _score(source: str, position: int) -> float:
    """درجة أولوية: وزن المصدر ناقص تلاشٍ حسب الترتيب."""
    base = SOURCE_WEIGHT.get(source, 0.5)
    decay = min(0.4, position / 2000.0)
    return round(max(0.05, base - decay), 4)


def _row_from_short(short: Any, source: str, position: int) -> Optional[Dict[str, Any]]:
    pk = str(getattr(short, "pk", "") or "")
    username = str(getattr(short, "username", "") or "")
    if not pk or not username:
        return None
    return Target(
        pk=pk,
        username=username,
        source=source,
        full_name=str(getattr(short, "full_name", "") or ""),
        is_private=bool(getattr(short, "is_private", False)),
        is_verified=bool(getattr(short, "is_verified", False)),
        has_profile_pic=bool(getattr(short, "profile_pic_url", None)),
        score=_score(source, position),
        harvested_at=utcnow(),
    ).to_row()


class Harvester:
    """ينفّذ الجمع مع احترام حصة harvest."""

    def __init__(self, client: IgClient, db: Database, cfg: Config, quota: QuotaManager) -> None:
        self.client = client
        self.db = db
        self.cfg = cfg
        self.quota = quota
        self.self_pk: Optional[str] = None

    def _self(self) -> str:
        if not self.self_pk:
            self.self_pk = self.client.self_pk()
        return self.self_pk

    def _store(self, rows: Iterable[Dict[str, Any]]) -> int:
        batch = [r for r in rows if r and r["pk"] != self._self()]
        if not batch:
            return 0
        inserted = self.db.add_targets(batch)
        if inserted:
            self.quota.consume("harvest", inserted)
            self.db.record_action(None, "harvest", True, meta={"inserted": inserted})
        return inserted

    def _cap(self) -> int:
        return min(self.cfg.targets.harvest_per_source, self.quota.remaining("harvest"))

    # ---------- المصادر ----------
    def from_seed_followers(self, username: str) -> int:
        cap = self._cap()
        if cap <= 0:
            return 0
        pk = self.client.user_id_from_username(username)
        followers = self.client.user_followers(pk, amount=cap)
        rows = [_row_from_short(s, HarvestSource.SEED_FOLLOWERS.value, i) for i, s in enumerate(followers.values())]
        return self._store(rows)

    def from_seed_following(self, username: str) -> int:
        cap = self._cap()
        if cap <= 0:
            return 0
        pk = self.client.user_id_from_username(username)
        following = self.client.user_following(pk, amount=cap)
        rows = [_row_from_short(s, HarvestSource.SEED_FOLLOWING.value, i) for i, s in enumerate(following.values())]
        return self._store(rows)

    def from_hashtag(self, tag: str) -> int:
        cap = self._cap()
        if cap <= 0:
            return 0
        medias = self.client.hashtag_medias_recent(tag, amount=min(30, cap))
        rows: List[Optional[Dict[str, Any]]] = []
        position = 0
        for media in medias:
            if self.quota.remaining("harvest") <= 0:
                break
            try:
                likers = self.client.media_likers(str(media.id))
            except Exception as exc:  # noqa: BLE001
                log.debug("تخطي منشور %s: %s", getattr(media, "id", "?"), exc)
                continue
            for short in likers:
                rows.append(_row_from_short(short, HarvestSource.HASHTAG.value, position))
                position += 1
                if position >= cap:
                    break
            if position >= cap:
                break
        return self._store(rows)

    def from_location(self, name: str) -> int:
        cap = self._cap()
        if cap <= 0:
            return 0
        location_pk = self.client.location_pk(name)
        medias = self.client.location_medias_recent(location_pk, amount=min(30, cap))
        rows: List[Optional[Dict[str, Any]]] = []
        position = 0
        for media in medias:
            if self.quota.remaining("harvest") <= 0:
                break
            try:
                likers = self.client.media_likers(str(media.id))
            except Exception:  # noqa: BLE001
                continue
            for short in likers:
                rows.append(_row_from_short(short, HarvestSource.LOCATION.value, position))
                position += 1
                if position >= cap:
                    break
            if position >= cap:
                break
        return self._store(rows)

    def from_post(self, url: str) -> int:
        cap = self._cap()
        if cap <= 0:
            return 0
        media_id = self.client.media_id(url)
        rows: List[Optional[Dict[str, Any]]] = []
        position = 0
        try:
            for short in self.client.media_likers(media_id):
                rows.append(_row_from_short(short, HarvestSource.POST_LIKERS.value, position))
                position += 1
                if position >= cap:
                    break
        except Exception as exc:  # noqa: BLE001
            log.debug("تعذّر جمع معجبي المنشور: %s", exc)
        try:
            for short in self.client.media_commenters(media_id):
                rows.append(_row_from_short(short, HarvestSource.POST_COMMENTERS.value, position))
                position += 1
                if position >= cap:
                    break
        except Exception as exc:  # noqa: BLE001
            log.debug("تعذّر جمع معلّقي المنشور: %s", exc)
        return self._store(rows)

    # ---------- التنسيق ----------
    def run_once(self) -> int:
        """يمر على كل المصادر المحددة ويُرجع عدد المُدرج."""
        total = 0
        for seed in self.cfg.targets.seeds:
            if self.quota.remaining("harvest") <= 0:
                break
            try:
                total += self.from_seed_followers(seed)
            except Exception as exc:  # noqa: BLE001
                log.warning("فشل جمع متابعي %s: %s", seed, exc)
        for tag in self.cfg.targets.hashtags:
            if self.quota.remaining("harvest") <= 0:
                break
            try:
                total += self.from_hashtag(tag)
            except Exception as exc:  # noqa: BLE001
                log.warning("فشل جمع هاشتاغ %s: %s", tag, exc)
        for loc in self.cfg.targets.locations:
            if self.quota.remaining("harvest") <= 0:
                break
            try:
                total += self.from_location(loc)
            except Exception as exc:  # noqa: BLE001
                log.warning("فشل جمع موقع %s: %s", loc, exc)
        for post in self.cfg.targets.posts:
            if self.quota.remaining("harvest") <= 0:
                break
            try:
                total += self.from_post(post)
            except Exception as exc:  # noqa: BLE001
                log.warning("فشل جمع منشور %s: %s", post, exc)
        log.info("جمع %d هدفاً جديداً (المخزون: %d)", total, self.db.pool_size())
        return total
