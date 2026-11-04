"""حفظ/تحميل جلسة إنستغرام مع صلاحيات 0600 وتشفير اختياري."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:  # اختياري
    from cryptography.fernet import Fernet, InvalidToken

    _HAS_CRYPTO = True
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment,misc]
    _HAS_CRYPTO = False

MAGIC = b"IRQ1"


class SessionError(RuntimeError):
    """فشل في قراءة أو كتابة الجلسة."""


def _key_from_env() -> Optional[bytes]:
    raw = os.environ.get("INSTARASHQ_SESSION_KEY", "").strip()
    if not raw:
        return None
    if not _HAS_CRYPTO:
        raise SessionError("INSTARASHQ_SESSION_KEY مضبوط لكن مكتبة cryptography غير مثبتة")
    return raw.encode("utf-8")


def _fernet() -> Optional["Fernet"]:
    key = _key_from_env()
    return Fernet(key) if key else None


def save_session(path: str, settings: Dict[str, Any]) -> Path:
    """يكتب الجلسة بأمان (0600) مع تشفير إن وُجد مفتاح."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings, ensure_ascii=False).encode("utf-8")
    fernet = _fernet()
    if fernet:
        payload = MAGIC + fernet.encrypt(payload)
    tmp = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.replace(tmp, target)
    os.chmod(target, 0o600)
    return target


def load_session(path: str) -> Optional[Dict[str, Any]]:
    """يقرأ الجلسة إن وُجدت، ويفك التشفير عند الحاجة."""
    target = Path(path).expanduser()
    if not target.is_file():
        return None
    payload = target.read_bytes()
    if payload.startswith(MAGIC):
        fernet = _fernet()
        if not fernet:
            raise SessionError("الجلسة مشفّرة لكن INSTARASHQ_SESSION_KEY غير مضبوط")
        try:
            payload = fernet.decrypt(payload[len(MAGIC):])
        except InvalidToken as exc:
            raise SessionError("مفتاح الجلسة غير صحيح") from exc
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SessionError(f"ملف جلسة تالف: {target}") from exc
    if not isinstance(data, dict):
        raise SessionError("محتوى الجلسة ليس كائناً JSON")
    return data


def clear_session(path: str) -> bool:
    target = Path(path).expanduser()
    if target.is_file():
        target.unlink()
        return True
    return False
