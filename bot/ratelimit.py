"""Ограничение частоты запросов: защита от перебора паролей/кодов, спама письмами и заявками.

Счётчики живут в памяти процесса (скользящее окно): API — один процесс, а после рестарта
окна просто начинаются заново, что для защиты от перебора не страшно.
"""
import time
from collections import defaultdict, deque

from aiohttp import web


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _window(self, key: str, window: float, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        return hits

    def allow(self, key: str, limit: int, window: float) -> bool:
        """Засчитывает попытку и говорит, укладывается ли она в лимит `limit` за `window` секунд."""
        now = time.monotonic()
        hits = self._window(key, window, now)
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def blocked(self, key: str, limit: int, window: float) -> bool:
        """Проверка без засчитывания — для счётчиков, которые растут только на неудачах (`fail`)."""
        return len(self._window(key, window, time.monotonic())) >= limit

    def fail(self, key: str) -> None:
        self._hits[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def sweep(self, max_window: float = 3600.0) -> None:
        """Выбрасывает давно неактивные ключи, чтобы словарь не рос бесконечно."""
        cutoff = time.monotonic() - max_window
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]


def client_ip(request: web.Request) -> str:
    """IP клиента. За прокси хостинга (Railway) настоящий адрес приходит в заголовках."""
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote or "unknown"


def enforce(request: web.Request, key: str, limit: int, window: float) -> None:
    """429, если лимит исчерпан. Лимитер лежит в app["limiter"]."""
    limiter: RateLimiter = request.app["limiter"]
    if not limiter.allow(key, limit, window):
        raise web.HTTPTooManyRequests(reason="too many requests, try again later")
