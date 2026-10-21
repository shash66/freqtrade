"""Single-administrator HTTP Basic auth for the MVP, plus a simple Redis-backed rate limiter."""
from __future__ import annotations

import secrets
import time

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import Settings, get_settings

_basic = HTTPBasic(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def require_admin(
    credentials: HTTPBasicCredentials | None = Depends(_basic),
    settings: Settings = Depends(get_settings),
) -> str:
    if not settings.admin_password_hash:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_PASSWORD_HASH is not configured; set it before using authenticated endpoints.",
        )
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})
    user_ok = secrets.compare_digest(credentials.username.encode(), settings.admin_username.encode())
    if not (user_ok and verify_password(credentials.password, settings.admin_password_hash)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid credentials", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


class RateLimiter:
    """Fixed-window limiter. Uses Redis when available, falls back to in-process memory."""

    def __init__(self, per_minute: int, redis_client=None):
        self.per_minute = per_minute
        self.redis = redis_client
        self._mem: dict[str, tuple[int, int]] = {}

    def check(self, key: str) -> None:
        window = int(time.time() // 60)
        if self.redis is not None:
            try:
                rk = f"ratelimit:{key}:{window}"
                n = self.redis.incr(rk)
                if n == 1:
                    self.redis.expire(rk, 65)
            except Exception:  # Redis down must not take the API down
                n = self._mem_incr(key, window)
        else:
            n = self._mem_incr(key, window)
        if n > self.per_minute:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded")

    def _mem_incr(self, key: str, window: int) -> int:
        w, n = self._mem.get(key, (window, 0))
        if w != window:
            n = 0
        n += 1
        self._mem[key] = (window, n)
        return n


def rate_limit(request: Request) -> None:
    limiter: RateLimiter = request.app.state.rate_limiter
    client = request.client.host if request.client else "unknown"
    limiter.check(client)
