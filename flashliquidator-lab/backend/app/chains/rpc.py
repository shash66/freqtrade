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
    def __init__(self, slug: str, url: str, timeout: float = 10.0, max_retries: int = 3):
        self.slug = slug
        self.url = url
        self.timeout = timeout
        self.max_retries = max_retries
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

    def call(self, fn, *args, **kwargs):
        """Run fn with retries on transient RPC errors. Caller decides what is retryable by catching."""
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except (Web3Exception, OSError, ValueError) as e:  # ValueError: JSON-RPC error payloads
                last = e
                msg = str(e)
                if "429" in msg or "rate" in msg.lower():
                    time.sleep(min(2.0 * attempt, 8.0))
                elif attempt < self.max_retries:
                    time.sleep(0.5 * attempt)
                log.warning("rpc_call_failed", network=self.slug, attempt=attempt, error=msg[:200])
        raise RpcUnavailable(f"{self.slug}: {last}") from last

    def health(self) -> RpcHealth:
        if not self.configured:
            return RpcHealth(False, None, None, None, "not configured")
        t0 = time.monotonic()
        try:
            cid = self.w3.eth.chain_id
            bn = self.w3.eth.block_number
            return RpcHealth(True, int((time.monotonic() - t0) * 1000), bn, cid, None)
        except Exception as e:  # any failure is a health failure, reported, not raised
            return RpcHealth(False, int((time.monotonic() - t0) * 1000), None, None, str(e)[:300])
