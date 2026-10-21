"""Indexer logic against the real database with a fake chain: duplicate events, reorg detection and rollback."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.chains.config import build_networks
from app.core.config import get_settings
from app.database import models as m
from app.database.base import get_sessionmaker
from app.indexer import pool_indexer as pi


class FakeEth:
    def __init__(self, hashes: dict[int, str]):
        self.hashes = hashes
        self.block_number = max(hashes)

    def get_block(self, n):
        return {"hash": bytes.fromhex(self.hashes[n][2:]), "timestamp": 1_700_000_000 + n}

    def get_logs(self, _):
        return []


class FakeW3:
    def __init__(self, hashes):
        self.eth = FakeEth(hashes)

    def keccak(self, text):
        from web3 import Web3
        return Web3.keccak(text=text)

    codec = None


class FakeClient:
    configured = True

    def __init__(self, hashes):
        self.w3 = FakeW3(hashes)

    def call(self, fn, *a, **k):
        return fn(*a, **k)


def _h(n: int, fork: str = "a") -> str:
    return "0x" + (f"{n:08x}{fork}".ljust(64, "0"))


@pytest.fixture
def indexer():
    get_settings.cache_clear()
    net = build_networks(get_settings())["base"]
    # unique pool address per test so rows never collide with other tests/runs
    from dataclasses import replace
    fake_pool = "0x" + uuid.uuid4().hex + "00000000"
    net = replace(net, market=replace(net.market, pool=fake_pool[:42]))
    hashes = {n: _h(n) for n in range(100, 121)}
    db = get_sessionmaker()()
    ix = pi.PoolIndexer.__new__(pi.PoolIndexer)
    ix.net, ix.client, ix.db, ix.s = net, FakeClient(hashes), db, get_settings()
    ix.w3 = ix.client.w3
    ix.pool_addr = net.market.pool
    ix.deployment = ix._ensure_deployment()
    yield ix
    db.close()


def _liq(ix, block, idx=0, tx=None):
    tx = tx or ("0x" + uuid.uuid4().hex + uuid.uuid4().hex)
    return pi.DecodedLog(block_number=block, block_hash=_h(block), tx_hash=tx, log_index=idx, event="LiquidationCall",
                         args={"collateralAsset": "0x" + "c" * 40, "debtAsset": "0x" + "d" * 40, "user": "0x" + "1" * 40,
                               "debtToCover": "1000000", "liquidatedCollateralAmount": "500000000000000000",
                               "liquidator": "0x" + "9" * 40, "receiveAToken": False})


def test_store_is_idempotent_and_discovers_borrowers(indexer):
    ev = _liq(indexer, 110)
    r1 = indexer.store([ev])
    r2 = indexer.store([ev])  # same (chain, tx, log_index) again
    assert r1["events"] == 1 and r1["liquidations"] == 1 and r2["events"] == 1
    n_events = indexer.db.scalar(select(m.PoolEvent).where(m.PoolEvent.tx_hash == ev.tx_hash).with_only_columns(m.PoolEvent.id)
                                 .count()) if False else len(indexer.db.scalars(select(m.PoolEvent).where(m.PoolEvent.tx_hash == ev.tx_hash)).all())
    assert n_events == 1
    liqs = indexer.db.scalars(select(m.HistoricalLiquidation).where(m.HistoricalLiquidation.tx_hash == ev.tx_hash)).all()
    assert len(liqs) == 1 and liqs[0].debt_repaid_raw == 1_000_000 and liqs[0].gas_used is None  # no receipt in fake chain -> NULL
    b = indexer.db.scalar(select(m.Borrower).where(m.Borrower.deployment_id == indexer.deployment.id))
    assert b is not None and b.address == "0x" + "1" * 40 and b.first_seen_block == 110


def test_reorg_detection_and_rollback(indexer):
    db = indexer.db
    cp = m.IndexerCheckpoint(chain_id=indexer.net.chain_id, deployment_id=indexer.deployment.id, stream=pi.STREAM,
                             last_block=120, last_block_hash=_h(120), recent_block_hashes={str(n): _h(n) for n in range(100, 121)})
    db.add(cp)
    db.commit()
    ev_old, ev_new = _liq(indexer, 112), _liq(indexer, 118, idx=1)
    indexer.store([ev_old, ev_new])
    assert indexer.check_reorg(cp) is None  # chain agrees with us
    # chain reorganizes from block 116 onward
    for n in range(116, 121):
        indexer.client.w3.eth.hashes[n] = _h(n, fork="b")
    ancestor = indexer.check_reorg(cp)
    assert ancestor == 115
    orphaned = indexer.rollback(cp, ancestor)
    assert orphaned == 1
    db.expire_all()
    rows = {r.block_number: r.orphaned for r in db.scalars(select(m.PoolEvent).where(m.PoolEvent.deployment_id == indexer.deployment.id))}
    assert rows == {112: False, 118: True}
    assert cp.last_block == 115 and cp.last_block_hash == _h(115)
    assert max(int(k) for k in cp.recent_block_hashes) == 115
    # re-indexing the same event after the reorg un-orphans it (idempotent upsert)
    indexer.store([ev_new])
    db.expire_all()
    assert db.scalar(select(m.PoolEvent.orphaned).where(m.PoolEvent.tx_hash == ev_new.tx_hash)) is False
