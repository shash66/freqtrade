"""Thin JSON-RPC client around web3.py with timeouts, bounded retries and health reporting.
One client per network: a failing network never blocks another."""
from __future__ import annotations

import time
from dataclasses import dataclass

from web3 import Web3
from web3.exceptions import Web3Exception
from web3.middleware import ExtraDataToPOAMiddleware

from app.core.logging import get_logger

log = get_logger(__name__)


class RpcUnavailable(RuntimeError):
    pass


@dataclass
class RpcHealth:
    ok: bool
    latency_ms: int | None
    block_number: int | None
    chain_id: int | None
    error: str | None


class RpcClient:
    def __init__(self, slug: str, url: str, timeout: float = 10.0, max_retries: int = 3,
                 rate_limit_retries: int | None = None, min_interval_ms: int | None = None):
        from app.core.config import get_settings
        s = get_settings()
        self.slug = slug
        self.url = url
        self.timeout = timeout
        self.max_retries = max_retries
        self.rate_limit_retries = s.rpc_rate_limit_retries if rate_limit_retries is None else rate_limit_retries
        self.min_interval = (s.rpc_min_interval_ms if min_interval_ms is None else min_interval_ms) / 1000.0
        self._last_call = 0.0
        self._w3: Web3 | None = None

    @property
    def configured(self) -> bool:
        return bool(self.url)

    @property
    def w3(self) -> Web3:
        if not self.configured:
            raise RpcUnavailable(f"{self.slug}: no RPC endpoint configured")
        if self._w3 is None:
            w3 = Web3(Web3.HTTPProvider(self.url, request_kwargs={"timeout": self.timeout}))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            self._w3 = w3
        return self._w3

    def _throttle(self) -> None:
        if self.min_interval > 0:
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        self._last_call = time.monotonic()

    def call(self, fn, *args, **kwargs):
        """Run fn with retries. Transient errors get a few quick retries; rate-limit / quota responses get a
        longer exponential backoff (public endpoints), after which RpcUnavailable is raised."""
        last: Exception | None = None
        attempt = rl_attempt = 0
        while True:
            self._throttle()
            try:
                return fn(*args, **kwargs)
            except (Web3Exception, OSError, ValueError) as e:  # ValueError: JSON-RPC error payloads
                msg = str(e)
                body = getattr(getattr(e, "response", None), "text", None)  # HTTP errors: include the provider's message
                if body:
                    msg = f"{msg} :: {body[:300]}"
                last = RuntimeError(msg) if body else e
                lower = msg.lower()
                rate_limited = "429" in msg or "too many requests" in lower or "limit reached" in lower or "endpoint limit" in lower or "rate" in lower
                if rate_limited and rl_attempt < self.rate_limit_retries:
                    rl_attempt += 1
                    delay = min(2.0 ** rl_attempt * 2.5, 60.0)  # 5, 10, 20, 40, 60, 60 s
                    log.warning("rpc_rate_limited", network=self.slug, backoff_s=delay, attempt=rl_attempt, error=msg[:160])
                    time.sleep(delay)
                    continue
                attempt += 1
                if not rate_limited and attempt < self.max_retries:
                    log.warning("rpc_call_failed", network=self.slug, attempt=attempt, error=msg[:200])
                    time.sleep(0.5 * attempt)
                    continue
                raise RpcUnavailable(f"{self.slug}: {last}") from last

    def health(self) -> RpcHealth:
        if not self.configured:
            return RpcHealth(False, None, None, None, "not configured")
        t0 = time.monotonic()
        try:
            cid = self.call(lambda: self.w3.eth.chain_id)      # through call(): rate-limit backoff applies
            bn = self.call(lambda: self.w3.eth.block_number)
            return RpcHealth(True, int((time.monotonic() - t0) * 1000), bn, cid, None)
        except Exception as e:  # any failure is a health failure, reported, not raised
            return RpcHealth(False, int((time.monotonic() - t0) * 1000), None, None, str(e)[:300])
