"""تحميل الإعدادات والتحقق منها وتجاوزات البيئة."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
HASHTAG_RE = re.compile(r"^[^\s#]{1,100}$")
PROXY_RE = re.compile(r"^(https?|socks5h?)://", re.IGNORECASE)


class ConfigError(ValueError):
    """إعدادات مفقودة أو غير صالحة."""


@dataclass
class AccountConfig:
    username: str = ""
    password: str = ""
    session_file: str = ".sessions/default.json"
    totp_secret: Optional[str] = None
    proxy: Optional[str] = None
    locale: str = "en_US"
    timezone_offset: int = 0


@dataclass
class LimitsConfig:
    warmup_days: int = 14
    daily_follow: int = 180
    daily_unfollow: int = 180
    daily_like: int = 400
    daily_comment: int = 60
    daily_harvest: int = 2000
    daily_enrich: int = 400
    hourly_follow: int = 25
    hourly_unfollow: int = 25
    hourly_like: int = 60
    hourly_comment: int = 10
    hourly_harvest: int = 400
    hourly_enrich: int = 80
    max_following_total: int = 7500

    def daily(self, action: str) -> int:
        return int(getattr(self, f"daily_{action}", 0))

    def hourly(self, action: str) -> int:
        return int(getattr(self, f"hourly_{action}", 0))


@dataclass
class PacingConfig:
    min_delay: int = 25
    max_delay: int = 90
    break_every: int = 40
    break_min: int = 300
    break_max: int = 900
    active_hours: List[int] = field(default_factory=lambda: [8, 23])
    active_days: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    timezone: str = "Asia/Baghdad"


@dataclass
class FiltersConfig:
    min_followers: int = 50
    max_followers: int = 50000
    skip_private: bool = True
    skip_verified: bool = True
    skip_business: bool = False
    require_profile_pic: bool = True
    min_posts: int = 3
    max_following_ratio: float = 3.0
    languages: List[str] = field(default_factory=list)


@dataclass
class TargetsConfig:
    seeds: List[str] = field(default_factory=list)
    hashtags: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    posts: List[str] = field(default_factory=list)
    min_pool: int = 200
    harvest_per_source: int = 300
    filters: FiltersConfig = field(default_factory=FiltersConfig)


@dataclass
class UnfollowConfig:
    enabled: bool = True
    min_age_hours: int = 48
    whitelist: List[str] = field(default_factory=list)
    keep_followers: bool = True
    refresh_followers_every: int = 50


@dataclass
class CommentsConfig:
    enabled: bool = False
    per_target: int = 1
    templates: List[str] = field(default_factory=list)


@dataclass
class StorageConfig:
    db_path: str = "data/instarashq.db"
    export_dir: str = "exports"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/instarashq.log"


@dataclass
class Config:
    account: AccountConfig = field(default_factory=AccountConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    pacing: PacingConfig = field(default_factory=PacingConfig)
    targets: TargetsConfig = field(default_factory=TargetsConfig)
    unfollow: UnfollowConfig = field(default_factory=UnfollowConfig)
    comments: CommentsConfig = field(default_factory=CommentsConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    source_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("source_path", None)
        return data


def _as_int(value: Any, name: str, minimum: int = 0) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} يجب أن يكون عدداً صحيحاً، وصل: {value!r}") from exc
    if out < minimum:
        raise ConfigError(f"{name} يجب أن يكون >= {minimum}، وصل: {out}")
    return out


def _as_float(value: Any, name: str, minimum: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} يجب أن يكون رقماً، وصل: {value!r}") from exc
    if out < minimum:
        raise ConfigError(f"{name} يجب أن يكون >= {minimum}، وصل: {out}")
    return out


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"1", "true", "yes", "on"}:
            return True
        if low in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int):
        return bool(value)
    raise ConfigError(f"{name} يجب أن يكون قيمة منطقية، وصل: {value!r}")


def _as_str_list(value: Any, name: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{name} يجب أن يكون قائمة، وصل: {type(value).__name__}")
    out: List[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _clean_username(value: str) -> str:
    return value.strip().lstrip("@").strip()


def _build_filters(raw: Dict[str, Any]) -> FiltersConfig:
    cfg = FiltersConfig()
    if not raw:
        return cfg
    cfg.min_followers = _as_int(raw.get("min_followers", cfg.min_followers), "targets.filters.min_followers")
    cfg.max_followers = _as_int(raw.get("max_followers", cfg.max_followers), "targets.filters.max_followers")
    if cfg.max_followers < cfg.min_followers:
        raise ConfigError("targets.filters.max_followers يجب أن يكون >= min_followers")
    cfg.skip_private = _as_bool(raw.get("skip_private", cfg.skip_private), "targets.filters.skip_private")
    cfg.skip_verified = _as_bool(raw.get("skip_verified", cfg.skip_verified), "targets.filters.skip_verified")
    cfg.skip_business = _as_bool(raw.get("skip_business", cfg.skip_business), "targets.filters.skip_business")
    cfg.require_profile_pic = _as_bool(
        raw.get("require_profile_pic", cfg.require_profile_pic), "targets.filters.require_profile_pic"
    )
    cfg.min_posts = _as_int(raw.get("min_posts", cfg.min_posts), "targets.filters.min_posts")
    cfg.max_following_ratio = _as_float(
        raw.get("max_following_ratio", cfg.max_following_ratio), "targets.filters.max_following_ratio", 0.1
    )
    cfg.languages = [x.lower() for x in _as_str_list(raw.get("languages"), "targets.filters.languages")]
    return cfg


def _build_targets(raw: Dict[str, Any]) -> TargetsConfig:
    cfg = TargetsConfig()
    if not raw:
        return cfg
    cfg.seeds = [_clean_username(u) for u in _as_str_list(raw.get("seeds"), "targets.seeds")]
    for seed in cfg.seeds:
        if not USERNAME_RE.match(seed):
            raise ConfigError(f"اسم مستخدم غير صالح في targets.seeds: {seed!r}")
    cfg.hashtags = [h.lstrip("#") for h in _as_str_list(raw.get("hashtags"), "targets.hashtags")]
    for tag in cfg.hashtags:
        if not HASHTAG_RE.match(tag):
            raise ConfigError(f"هاشتاغ غير صالح في targets.hashtags: {tag!r}")
    cfg.locations = _as_str_list(raw.get("locations"), "targets.locations")
    cfg.posts = _as_str_list(raw.get("posts"), "targets.posts")
    cfg.min_pool = _as_int(raw.get("min_pool", cfg.min_pool), "targets.min_pool", 1)
    cfg.harvest_per_source = _as_int(
        raw.get("harvest_per_source", cfg.harvest_per_source), "targets.harvest_per_source", 1
    )
    cfg.filters = _build_filters(raw.get("filters") or {})
    return cfg


def _build_pacing(raw: Dict[str, Any]) -> PacingConfig:
    cfg = PacingConfig()
    if not raw:
        return cfg
    cfg.min_delay = _as_int(raw.get("min_delay", cfg.min_delay), "pacing.min_delay", 1)
    cfg.max_delay = _as_int(raw.get("max_delay", cfg.max_delay), "pacing.max_delay", 1)
    if cfg.max_delay < cfg.min_delay:
        raise ConfigError("pacing.max_delay يجب أن يكون >= min_delay")
    cfg.break_every = _as_int(raw.get("break_every", cfg.break_every), "pacing.break_every")
    cfg.break_min = _as_int(raw.get("break_min", cfg.break_min), "pacing.break_min")
    cfg.break_max = _as_int(raw.get("break_max", cfg.break_max), "pacing.break_max")
    if cfg.break_max < cfg.break_min:
        raise ConfigError("pacing.break_max يجب أن يكون >= break_min")
    hours = raw.get("active_hours", cfg.active_hours)
    if not isinstance(hours, (list, tuple)) or len(hours) != 2:
        raise ConfigError("pacing.active_hours يجب أن يكون [بداية, نهاية]")
    start, end = _as_int(hours[0], "pacing.active_hours[0]"), _as_int(hours[1], "pacing.active_hours[1]")
    if not (0 <= start <= 23 and 0 <= end <= 24):
        raise ConfigError("pacing.active_hours خارج النطاق 0..24")
    cfg.active_hours = [start, end]
    days = raw.get("active_days", cfg.active_days)
    if not isinstance(days, (list, tuple)):
        raise ConfigError("pacing.active_days يجب أن يكون قائمة")
    parsed_days = sorted({_as_int(d, "pacing.active_days[]") for d in days})
    for day in parsed_days:
        if not 0 <= day <= 6:
            raise ConfigError("pacing.active_days يجب أن تكون بين 0 و6")
    cfg.active_days = parsed_days or [0, 1, 2, 3, 4, 5, 6]
    cfg.timezone = str(raw.get("timezone", cfg.timezone)).strip() or "Asia/Baghdad"
    return cfg


def _build_limits(raw: Dict[str, Any]) -> LimitsConfig:
    cfg = LimitsConfig()
    if not raw:
        return cfg
    for name in (
        "warmup_days",
        "daily_follow",
        "daily_unfollow",
        "daily_like",
        "daily_comment",
        "daily_harvest",
        "daily_enrich",
        "hourly_follow",
        "hourly_unfollow",
        "hourly_like",
        "hourly_comment",
        "hourly_harvest",
        "hourly_enrich",
        "max_following_total",
    ):
        if name in raw:
            setattr(cfg, name, _as_int(raw[name], f"limits.{name}"))
    for action in ("follow", "unfollow", "like", "comment", "harvest", "enrich"):
        if cfg.hourly(action) > cfg.daily(action):
            raise ConfigError(f"limits.hourly_{action} يجب أن يكون <= daily_{action}")
    return cfg


def _build_unfollow(raw: Dict[str, Any]) -> UnfollowConfig:
    cfg = UnfollowConfig()
    if not raw:
        return cfg
    cfg.enabled = _as_bool(raw.get("enabled", cfg.enabled), "unfollow.enabled")
    cfg.min_age_hours = _as_int(raw.get("min_age_hours", cfg.min_age_hours), "unfollow.min_age_hours")
    cfg.whitelist = [_clean_username(u) for u in _as_str_list(raw.get("whitelist"), "unfollow.whitelist")]
    cfg.keep_followers = _as_bool(raw.get("keep_followers", cfg.keep_followers), "unfollow.keep_followers")
    cfg.refresh_followers_every = _as_int(
        raw.get("refresh_followers_every", cfg.refresh_followers_every), "unfollow.refresh_followers_every", 1
    )
    return cfg


def _build_comments(raw: Dict[str, Any]) -> CommentsConfig:
    cfg = CommentsConfig()
    if not raw:
        return cfg
    cfg.enabled = _as_bool(raw.get("enabled", cfg.enabled), "comments.enabled")
    cfg.per_target = _as_int(raw.get("per_target", cfg.per_target), "comments.per_target")
    cfg.templates = _as_str_list(raw.get("templates"), "comments.templates")
    if cfg.enabled and not cfg.templates:
        raise ConfigError("comments.enabled=true يتطلب قائمة comments.templates غير فارغة")
    return cfg


def _build_account(raw: Dict[str, Any]) -> AccountConfig:
    cfg = AccountConfig()
    if not raw:
        return cfg
    cfg.username = _clean_username(str(raw.get("username") or ""))
    cfg.password = str(raw.get("password") or "")
    cfg.session_file = str(raw.get("session_file") or cfg.session_file)
    totp = raw.get("totp_secret")
    cfg.totp_secret = str(totp).strip() if totp else None
    proxy = raw.get("proxy")
    cfg.proxy = str(proxy).strip() if proxy else None
    cfg.locale = str(raw.get("locale") or cfg.locale)
    cfg.timezone_offset = _as_int(raw.get("timezone_offset", cfg.timezone_offset), "account.timezone_offset")
    return cfg


def _apply_env(cfg: Config) -> None:
    env = os.environ
    if env.get("INSTARASHQ_USERNAME"):
        cfg.account.username = _clean_username(env["INSTARASHQ_USERNAME"])
    if env.get("INSTARASHQ_PASSWORD"):
        cfg.account.password = env["INSTARASHQ_PASSWORD"]
    if env.get("INSTARASHQ_PROXY"):
        cfg.account.proxy = env["INSTARASHQ_PROXY"].strip()
    if env.get("INSTARASHQ_SESSION_FILE"):
        cfg.account.session_file = env["INSTARASHQ_SESSION_FILE"]
    if env.get("INSTARASHQ_DB"):
        cfg.storage.db_path = env["INSTARASHQ_DB"]
    if env.get("INSTARASHQ_LOG_LEVEL"):
        cfg.logging.level = env["INSTARASHQ_LOG_LEVEL"].upper()


def validate(cfg: Config, require_credentials: bool = True) -> None:
    """تحقق نهائي قبل التشغيل."""
    if require_credentials:
        if not cfg.account.username:
            raise ConfigError("account.username مطلوب (أو INSTARASHQ_USERNAME)")
        if not USERNAME_RE.match(cfg.account.username):
            raise ConfigError(f"account.username غير صالح: {cfg.account.username!r}")
        if not cfg.account.password:
            raise ConfigError("account.password مطلوب (أو INSTARASHQ_PASSWORD)")
    if cfg.account.proxy and not PROXY_RE.match(cfg.account.proxy):
        raise ConfigError("account.proxy يجب أن يبدأ بـ http:// أو https:// أو socks5://")
    if not cfg.targets.seeds and not cfg.targets.hashtags and not cfg.targets.locations and not cfg.targets.posts:
        raise ConfigError("حدد مصدراً واحداً على الأقل: targets.seeds / hashtags / locations / posts")
    if cfg.logging.level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError(f"logging.level غير صالح: {cfg.logging.level!r}")


def load_config(path: Optional[str] = None, require_credentials: bool = True) -> Config:
    """يحمّل الإعدادات من YAML + متغيرات البيئة ثم يتحقق منها."""
    raw: Dict[str, Any] = {}
    resolved: Optional[str] = None
    if path:
        resolved = str(Path(path).expanduser())
        file_path = Path(resolved)
        if not file_path.is_file():
            raise ConfigError(f"ملف الإعدادات غير موجود: {resolved}")
        with file_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, dict):
            raise ConfigError("جذر ملف الإعدادات يجب أن يكون خريطة YAML")
        raw = loaded

    cfg = Config(source_path=resolved)
    cfg.account = _build_account(raw.get("account") or {})
    cfg.limits = _build_limits(raw.get("limits") or {})
    cfg.pacing = _build_pacing(raw.get("pacing") or {})
    cfg.targets = _build_targets(raw.get("targets") or {})
    cfg.unfollow = _build_unfollow(raw.get("unfollow") or {})
    cfg.comments = _build_comments(raw.get("comments") or {})
    storage = raw.get("storage") or {}
    if storage:
        cfg.storage.db_path = str(storage.get("db_path") or cfg.storage.db_path)
        cfg.storage.export_dir = str(storage.get("export_dir") or cfg.storage.export_dir)
    logging_raw = raw.get("logging") or {}
    if logging_raw:
        cfg.logging.level = str(logging_raw.get("level") or cfg.logging.level).upper()
        cfg.logging.file = str(logging_raw.get("file") or cfg.logging.file)

    _apply_env(cfg)
    validate(cfg, require_credentials=require_credentials)
    return cfg
