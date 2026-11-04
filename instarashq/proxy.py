"""إدارة بروكسي: تحليل، تدوير، فحص صحة."""
from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from typing import Iterable, List, Optional
from urllib.parse import urlparse

PROXY_RE = re.compile(r"^(?P<scheme>https?|socks5h?)://(?:(?P<user>[^:@/]+):(?P<pass>[^@/]+)@)?(?P<host>[^:/]+):(?P<port>\d{1,5})$")


class ProxyError(ValueError):
    """صيغة بروكسي غير صالحة."""


@dataclass
class Proxy:
    raw: str
    scheme: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    healthy: bool = True
    failures: int = 0

    def url(self) -> str:
        auth = ""
        if self.username:
            auth = f"{self.username}:{self.password or ''}@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    def __str__(self) -> str:
        return self.url()


def parse_proxy(value: str) -> Proxy:
    text = value.strip()
    if not text:
        raise ProxyError("بروكسي فارغ")
    if "://" not in text:
        text = f"http://{text}"
    match = PROXY_RE.match(text)
    if not match:
        raise ProxyError(f"صيغة بروكسي غير صالحة: {value!r}")
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise ProxyError(f"منفذ بروكسي خارج النطاق: {port}")
    return Proxy(
        raw=value,
        scheme=match.group("scheme").lower(),
        host=match.group("host"),
        port=port,
        username=match.group("user"),
        password=match.group("pass"),
    )


class ProxyPool:
    """تدوير بروكسي مع استبعاد الفاشلة."""

    def __init__(self, entries: Iterable[str] = ()) -> None:
        self._items: List[Proxy] = [parse_proxy(e) for e in entries if str(e).strip()]
        self._index = 0

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def add(self, value: str) -> None:
        self._items.append(parse_proxy(value))

    def next(self) -> Optional[Proxy]:
        if not self._items:
            return None
        for _ in range(len(self._items)):
            proxy = self._items[self._index % len(self._items)]
            self._index += 1
            if proxy.healthy:
                return proxy
        return None

    def mark_failure(self, proxy: Proxy, threshold: int = 3) -> None:
        proxy.failures += 1
        if proxy.failures >= threshold:
            proxy.healthy = False

    def mark_success(self, proxy: Proxy) -> None:
        proxy.failures = 0
        proxy.healthy = True

    def health_check(self, proxy: Proxy, timeout: float = 5.0) -> bool:
        try:
            with socket.create_connection((proxy.host, proxy.port), timeout=timeout):
                self.mark_success(proxy)
                return True
        except OSError:
            self.mark_failure(proxy)
            return False

    def check_all(self, timeout: float = 5.0) -> List[bool]:
        return [self.health_check(p, timeout) for p in self._items]
