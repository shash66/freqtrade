# Micro-trading strategy search: results (October 2026)

Plain-language record of an automated search for a trading strategy for a ~$20
Kraken spot account. **Short version: no strategy with a demonstrable edge was
found.** Read this before putting real money behind any of the files in
`user_data/strategies/WF_*.py`.

## What was tested

- Exchange and pairs: Kraken spot, XRP/USD, DOGE/USD, ADA/USD, TRX/USD, ETH/USD, SOL/USD.
- Account model: $20 wallet, $6 per trade, up to 3 trades open, **0.4% fee per side**
  (Kraken's taker fee for small accounts; 0.8% per round trip).
- Seven approaches, each written and tuned by a separate agent, plus the bundled
  `SampleStrategy` as a baseline:
  EMA trend-following, Bollinger mean reversion, Donchian breakout, RSI dip-buy
  with trend filter, MACD momentum, volatility squeeze breakout, trend pullback.

### Round 1: 1-hour candles, 30 days

Tuned on 15 Sept to 5 Oct, tested on 5 to 16 Oct (never seen during tuning).
The market fell 11% in the test window. **Every strategy that traded lost money;**
the only one that did not lose never traded. The window was too short to tell a
good strategy from a lucky one, so round 2 was run.

(Caveat: during this round a background download overwrote the hourly data for
XRP, DOGE and ADA with a shorter file. The test-window results were unaffected,
but tuning-period numbers for those three coins were partly missing.)

### Round 2: 4-hour candles, 4 months (the main result)

Tuned on 17 June to 16 Aug **only**, then run unchanged on three consecutive
unseen 20-day windows. Profit is % of the $20 wallet. "Market" is buy-and-hold
over the same window; holding cash is 0%.

| Strategy | Tuning (60 d) | A: 16 Aug–5 Sep | B: 5–25 Sep | C: 25 Sep–16 Oct | All 60 d unseen |
|---|---|---|---|---|---|
| Market (buy & hold) | about +7% | +24.3% | +10.9% | −9.1% | +25.2% |
| Sample (baseline) | +0.3% | +0.3% | +0.9% | −0.4% | +0.8% |
| EMA trend | +1.0% | +4.3% | −1.2% | −2.8% | −0.1% |
| Bollinger mean reversion | +4.5% | +2.2% | +3.6% | −7.4% | −1.7% |
| Breakout | −2.6% | +3.0% | −5.3% | −5.1% | −5.5% |
| RSI + trend filter | −0.6% | +0.9% | 0 trades | +1.8% | +1.8% |
| MACD momentum | +1.7% | −1.3% | −2.9% | −9.6% | −13.2% |
| **Volatility squeeze** | +2.8% | **+6.9%** | −0.3% | +0.8% | **+7.4%** |
| Trend pullback | +3.3% | −2.0% | −4.4% | −8.7% | −12.3% |

Nothing beat buy-and-hold in the two rising windows. The two strategies that
finished the unseen period in profit (squeeze, RSI + trend filter) were each
challenged by three independent sceptic agents:

- **Lookahead bias** (does the code peek at the future?): both passed. Freqtrade's
  own `lookahead-analysis` reported no bias.
- **Statistics**: both refuted. 2 and 9 trades are far too few; picking the best of
  8 candidates makes a 7-wins-of-9 record about a coin flip (53% chance by luck).
  A passive 30%-invested buy-and-hold would have earned about the same +7.5% as
  squeeze over the same 60 days.
- **Robustness**: both refuted. Changing squeeze's thresholds by ±20% cuts its profit
  from 7.4% to 0.3–1.3%; the RSI strategy produces zero trades at −20%.
  Squeeze did survive higher fees (0.6%: +7.2%) and one-trade-at-a-time (+5.0%).

Six of squeeze's nine trades happened in one five-day burst in August on three
coins that moved together, so it was really one bet, not nine.

## Honest conclusion

- No tested strategy has shown an edge that survives a sceptical review.
- On a $20 account the best result was +$1.47 over two months, with a $0.05 fee per
  round trip. At this scale nothing is distinguishable from noise.
- The least-bad candidate for continued **paper** trading is the volatility squeeze
  strategy (`user_data/strategies/WF_squeeze.py`, 4h candles): clean code, low
  drawdown (0.5%), and fees do not destroy it. That is a reason to watch it, not to
  fund it.
- The benchmark that matters is buy-and-hold. Over the 4 months tested, simply
  holding ETH or SOL would have returned about +40%.

## If you want to go further

1. Keep paper trading for weeks, not hours, and compare to holding cash and to
   buy-and-hold over the same days.
2. Run the same frozen strategies over a year or more of history on your own
   machine (Kraken's API only serves the last 720 candles per timeframe; longer
   history needs `freqtrade download-data --dl-trades`, which is slow, or a
   different data source). Do not judge a strategy on fewer than ~100 trades.
3. Treat any money you do use as the cost of learning, and never fund the account
   with more than you can lose.

Commands used (from the repo root, inside the venv):

```bash
freqtrade backtesting --config config.json --strategy WFSqueeze -i 4h \
  --timerange 20260816-20261016 --fee 0.004 --max-open-trades 3 --dry-run-wallet 20
freqtrade lookahead-analysis --config config.json --strategy WFSqueeze -i 4h \
  --timerange 20260617-20261016 --fee 0.004 --max-open-trades 3 --dry-run-wallet 20
```
