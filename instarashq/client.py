"""مغلّف instagrapi: تسجيل دخول، إعادة محاولة، تصنيف أخطاء."""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar

from .config import Config
from .proxy import Proxy
from .session import SessionError, load_session, save_session

T = TypeVar("T")
log = logging.getLogger("instarashq.client")


class ClientError(RuntimeError):
    """خطأ عميل عام."""


class FatalClientError(ClientError):
    """خطأ يتطلب تدخلاً (بيانات دخول، تحدي، حظر)."""


class RetryableClientError(ClientError):
    """خطأ عابر يستحق إعادة المحاولة."""


class LoginRequiredError(RetryableClientError):
    """الجلسة انتهت ويجب إعادة الدخول."""


def _import_instagrapi() -> Any:
    try:
        import instagrapi  # type: ignore
        from instagrapi import exceptions as ig_exc  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise FatalClientError("مكتبة instagrapi غير مثبتة. نفّذ: pip install -r requirements.txt") from exc
    return instagrapi, ig_exc


class IgClient:
    """واجهة عالية المستوى مع إعادة محاولة أسّية وتصنيف أخطاء."""

    def __init__(self, cfg: Config, proxy: Optional[Proxy] = None) -> None:
        self.cfg = cfg
        self.proxy = proxy
        self._cl: Any = None
        self._ig: Any = None
        self._exc: Any = None
        self._logged_in = False
        self.max_retries = 4
        self.base_backoff = 3.0

    # ---------- دورة الحياة ----------
    def _ensure_module(self) -> None:
        if self._ig is None:
            self._ig, self._exc = _import_instagrapi()

    def _build(self) -> Any:
        self._ensure_module()
        cl = self._ig.Client()
        cl.delay_range = [1, 3]
        cl.set_locale(self.cfg.account.locale)
        cl.set_timezone_offset(self.cfg.account.timezone_offset)
        if self.proxy:
            cl.set_proxy(self.proxy.url())
        elif self.cfg.account.proxy:
            cl.set_proxy(self.cfg.account.proxy)
        cl.challenge_code_handler = self._challenge_handler
        cl.change_password_handler = self._change_password_handler
        return cl

    def _challenge_handler(self, username: str, choice: Any) -> str:
        log.warning("تحدي أمني للحساب %s (الخيار: %s) — أدخل الرمز:", username, choice)
        return input("رمز التحدي: ").strip()

    def _change_password_handler(self, username: str, old: str, new: str) -> str:
        log.warning("إنستغرام طلب تغيير كلمة المرور للحساب %s", username)
        return new

    def login(self, force: bool = False) -> None:
        """يسجّل الدخول مع استعادة الجلسة إن أمكن."""
        if self._logged_in and not force:
            return
        self._ensure_module()
        cl = self._build()
        self._cl = cl

        if not force:
            try:
                settings = load_session(self.cfg.account.session_file)
            except SessionError as exc:
                log.warning("تعذّر تحميل الجلسة: %s — سيتم الدخول من جديد", exc)
                settings = None
            if settings:
                try:
                    cl.set_settings(settings)
                    cl.login(self.cfg.account.username, self.cfg.account.password)
                    self._logged_in = True
                    log.info("تم استئناف الجلسة المحفوظة")
                    return
                except Exception as exc:  # noqa: BLE001
                    log.warning("فشل استئناف الجلسة (%s) — دخول كامل", exc)

        try:
            if self.cfg.account.totp_secret:
                cl.totp_generate_code = self._totp_code  # type: ignore[attr-defined]
            cl.login(self.cfg.account.username, self.cfg.account.password)
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc

        self._logged_in = True
        self._persist()
        log.info("تم تسجيل الدخول بنجاح كـ %s", self.cfg.account.username)

    def _totp_code(self, seed: str) -> str:
        try:
            import pyotp  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise FatalClientError("pyotp مطلوب لدعم 2FA: pip install pyotp") from exc
        return pyotp.TOTP(seed).now()

    def _persist(self) -> None:
        if not self._cl:
            return
        try:
            save_session(self.cfg.account.session_file, self._cl.get_settings())
        except Exception as exc:  # noqa: BLE001
            log.warning("تعذّر حفظ الجلسة: %s", exc)

    def close(self) -> None:
        self._persist()
        self._logged_in = False

    # ---------- تصنيف الأخطاء ----------
    def _classify(self, exc: Exception) -> ClientError:
        self._ensure_module()
        name = type(exc).__name__
        fatal = {
            "BadPassword",
            "ChallengeRequired",
            "TwoFactorRequired",
            "ProxyAddressIsBlocked",
            "ReloginAttemptExceeded",
            "AccountSuspended",
            "CaptchaChallengeRequired",
        }
        retry = {
            "PleaseWaitFewMinutes",
            "RateLimitError",
            "ClientConnectionError",
            "ClientTimeout",
            "InternalError",
            "ServiceUnavailable",
            "LoginRequired",
            "ClientThrottledError",
        }
        if name in fatal:
            return FatalClientError(f"{name}: {exc}")
        if name in retry:
            if name == "LoginRequired":
                return LoginRequiredError(str(exc))
            return RetryableClientError(f"{name}: {exc}")
        return ClientError(f"{name}: {exc}")

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """ينفّذ نداءً مع إعادة محاولة أسّية."""
        if not self._logged_in:
            self.login()
        last: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                classified = self._classify(exc)
                last = classified
                if isinstance(classified, FatalClientError):
                    raise classified from exc
                if isinstance(classified, LoginRequiredError):
                    log.warning("الجلسة انتهت — إعادة دخول")
                    self.login(force=True)
                    continue
                if attempt >= self.max_retries:
                    break
                backoff = self.base_backoff * (2 ** (attempt - 1)) + random.uniform(0, 2)
                log.warning("محاولة %d/%d فشلت (%s) — انتظار %.1fs", attempt, self.max_retries, classified, backoff)
                time.sleep(backoff)
        raise last or ClientError("فشل غير معروف")

    # ---------- عمليات ----------
    def user_id_from_username(self, username: str) -> str:
        return self.call(self._cl.user_id_from_username, username)

    def user_info(self, pk: str) -> Any:
        return self.call(self._cl.user_info, pk)

    def user_followers(self, pk: str, amount: int = 0) -> Dict[str, Any]:
        return self.call(self._cl.user_followers, pk, amount=amount)

    def user_following(self, pk: str, amount: int = 0) -> Dict[str, Any]:
        return self.call(self._cl.user_following, pk, amount=amount)

    def user_follow(self, pk: str) -> bool:
        return bool(self.call(self._cl.user_follow, pk))

    def user_unfollow(self, pk: str) -> bool:
        return bool(self.call(self._cl.user_unfollow, pk))

    def media_likers(self, media_id: str) -> List[Any]:
        return list(self.call(self._cl.media_likers, media_id))

    def media_commenters(self, media_id: str) -> List[Any]:
        return list(self.call(self._cl.media_commenters, media_id))

    def media_id(self, code_or_url: str) -> str:
        return self.call(self._cl.media_id, code_or_url)

    def media_like(self, media_id: str) -> bool:
        return bool(self.call(self._cl.media_like, media_id))

    def media_comment(self, media_id: str, text: str) -> Any:
        return self.call(self._cl.media_comment, media_id, text)

    def hashtag_medias_recent(self, tag: str, amount: int = 30) -> List[Any]:
        return list(self.call(self._cl.hashtag_medias_recent, tag, amount=amount))

    def location_medias_recent(self, location_pk: str, amount: int = 30) -> List[Any]:
        return list(self.call(self._cl.location_medias_recent, location_pk, amount=amount))

    def location_pk(self, name: str) -> str:
        results = self.call(self._cl.location_search, name)
        if not results:
            raise ClientError(f"موقع غير موجود: {name}")
        return str(results[0].pk)

    def self_pk(self) -> str:
        return str(self.call(lambda: self._cl.user_id))
