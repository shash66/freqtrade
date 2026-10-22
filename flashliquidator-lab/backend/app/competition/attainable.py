"""Modeled attainable net profit (Phase 4, priority 7): rank opportunities by what a strategy could expect to
keep, not by the gross liquidation bonus.

    attainable = p_capture × (net_profit − execution_cost_already_in_net) − (1 − p_capture) × failed_attempt_cost

`net_profit` is the complete-cost net profit of the opportunity (theoretical fast path or simulated full path;
it already includes gas, L1 data fee, premium and swap proceeds). `p_capture` combines

- the observability class of the opportunity (Phase 4 categories): publicly observable → the auction/FCFS win
  probability; predictable but not yet eligible → that probability × the measured share of such events that
  were still available when the Pool's price caught up; intra-block only → 0; unknown → 0;
- the priority-auction win probability for the chosen bid against the empirical distribution of winning bids
  on this Pool (`bidmodel.p_win_priority_auction`).

Every input is explicit in `AttainableInputs` and echoed in the result so a ranking can be reproduced. Where a
rate has not been measured (no sample), the factor is reported as unknown and attainable is null rather than
guessed; the caller may pass a prior and it is labeled as such.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal

from app.competition.bidmodel import p_win_priority_auction
from app.core.money import quantize_usd

CATEGORY_WEIGHTS_DEFAULT = {"publicly_observable": Decimal(1), "predictable_not_yet_eligible": None,  # None = needs the measured availability share
                            "intra_block_only": Decimal(0), "unknown": Decimal(0)}


@dataclass
class AttainableInputs:
    net_profit_usd: Decimal | None
    failed_attempt_cost_usd: Decimal | None
    category: str
    bid_wei: int | None
    winning_bids_wei: list[int]
    competitors_ready: int
    predictable_available_share: Decimal | None = None   # measured: share of predictable events still open when the Pool's price caught up
    predictable_available_share_source: str = "unmeasured"


@dataclass
class Attainable:
    attainable_net_profit_usd: Decimal | None
    p_capture: Decimal | None
    p_win_bid: Decimal | None
    category_weight: Decimal | None
    inputs: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("attainable_net_profit_usd", "p_capture", "p_win_bid", "category_weight"):
            d[k] = None if d[k] is None else str(d[k])
        return d


def attainable(i: AttainableInputs) -> Attainable:
    notes: list[str] = []
    w = CATEGORY_WEIGHTS_DEFAULT.get(i.category)
    if i.category == "predictable_not_yet_eligible":
        if i.predictable_available_share is None:
            return Attainable(None, None, None, None, _inputs(i), ["availability share of predictable events not measured; attainable unknown"])
        w = i.predictable_available_share
        notes.append(f"predictable event weighted by measured availability share {w} ({i.predictable_available_share_source})")
    if i.category not in CATEGORY_WEIGHTS_DEFAULT:
        return Attainable(None, None, None, None, _inputs(i), [f"unknown category {i.category}"])
    if i.net_profit_usd is None or i.failed_attempt_cost_usd is None:
        return Attainable(None, None, None, w, _inputs(i), ["net profit or failed-attempt cost unknown; attainable unknown"])
    if i.bid_wei is None or not i.winning_bids_wei:
        p_win = None
        if w == 0:
            p_win = Decimal(0)
        else:
            return Attainable(None, None, None, w, _inputs(i), ["no bid or no observed winning bids; win probability unknown"])
    else:
        p_win = Decimal(str(p_win_priority_auction(i.bid_wei, i.winning_bids_wei, i.competitors_ready)))
    p = (w * p_win).quantize(Decimal("0.0001"))
    value = quantize_usd(p * i.net_profit_usd - (Decimal(1) - p) * i.failed_attempt_cost_usd) if p > 0 else quantize_usd(-i.failed_attempt_cost_usd * 0)
    if p == 0:
        notes.append("not capturable by this strategy class: attainable is 0 (no attempt is modeled, so no failed-attempt cost)")
    return Attainable(value, p, p_win, w, _inputs(i), notes)


def _inputs(i: AttainableInputs) -> dict:
    return {"net_profit_usd": None if i.net_profit_usd is None else str(i.net_profit_usd),
            "failed_attempt_cost_usd": None if i.failed_attempt_cost_usd is None else str(i.failed_attempt_cost_usd),
            "category": i.category, "bid_wei": i.bid_wei, "winning_bids_n": len(i.winning_bids_wei), "competitors_ready": i.competitors_ready,
            "predictable_available_share": None if i.predictable_available_share is None else str(i.predictable_available_share),
            "predictable_available_share_source": i.predictable_available_share_source}


def rank(items: list[tuple[str, AttainableInputs]]) -> list[tuple[str, Attainable]]:
    """Sort by attainable net profit, unknowns last (they are never promoted by a missing number)."""
    scored = [(k, attainable(i)) for k, i in items]
    return sorted(scored, key=lambda kv: (kv[1].attainable_net_profit_usd is None, -(kv[1].attainable_net_profit_usd or Decimal(0))))
