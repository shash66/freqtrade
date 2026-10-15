# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
import numpy as np
import pandas as pd
from pandas import DataFrame

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter


class WFPullback(IStrategy):
    """
    4h trend-pullback candidate.

    Uptrend:  EMA(fast) > EMA(slow), close > EMA(slow), EMA(fast) rising.
    Entry:    close has pulled back to the fast EMA and is holding there
              (-0.5% <= (close - ema_fast)/ema_fast <= +1.5%), price was clearly
              above the fast EMA (> 1.5%) within the last 5 candles (so it is a
              genuine pullback rather than chop around the EMA), and RSI(14) is
              between 40 and 55 (resting, not oversold).
    Exit:     close < EMA(slow) (trend broken) or ROI targets / stoploss.

    Periods are adapted to 4h candles: EMA 10/30 ~ 1.7 / 5 days.
    In-sample notes: widening the band to +/-2% cut trades (the prior-excursion
    check got harder), and a longer lookback (8) only added losing "stale"
    pullbacks, so the tight 1.5% band with a 5-candle lookback is kept.
    """

    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI targets (minutes -> fraction). One 4h candle = 240 minutes.
    # Losers are capped by the EMA(slow) exit / stoploss, so winners must be
    # allowed to run; lower the bar as the trade ages.
    minimal_roi = {
        "0": 0.08,      # any time: +8%
        "1440": 0.04,   # after 1 day (6 candles): +4%
        "2880": 0.02,   # after 2 days (12 candles): +2%
    }

    # Entries sit near the fast EMA; a break below the slow EMA typically costs
    # a few percent on 4h, so the stoploss sits just beyond that.
    stoploss = -0.06

    trailing_stop = False

    # Warmup for EMA(30) + RSI(14). Hard limit is 60.
    startup_candle_count: int = 60

    # Parameters (plain round numbers; not hyperopted).
    ema_fast = IntParameter(5, 20, default=10, space="buy", optimize=False)
    ema_slow = IntParameter(20, 50, default=30, space="buy", optimize=False)
    rsi_period = IntParameter(10, 20, default=14, space="buy", optimize=False)
    rsi_low = IntParameter(30, 50, default=40, space="buy", optimize=False)
    rsi_high = IntParameter(50, 65, default=55, space="buy", optimize=False)
    # Allowed band around the fast EMA for the entry close (signed fraction).
    pullback_low = DecimalParameter(-0.02, 0.0, default=-0.005, decimals=3, space="buy", optimize=False)
    pullback_high = DecimalParameter(0.005, 0.03, default=0.015, decimals=3, space="buy", optimize=False)
    # Lookback (candles) in which price must have been > pullback_high above the
    # fast EMA, so the entry is a genuine pullback rather than chop.
    lookback = IntParameter(3, 10, default=5, space="buy", optimize=False)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=int(self.ema_fast.value))
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=int(self.ema_slow.value))
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=int(self.rsi_period.value))
        # Relative distance of close from fast EMA (signed; negative = below EMA).
        dataframe["ema_dist"] = (dataframe["close"] - dataframe["ema_fast"]) / dataframe["ema_fast"]
        # Was price clearly above the fast EMA recently? (backward-looking: prior candles only)
        dataframe["recent_max_dist"] = (
            dataframe["ema_dist"].shift(1).rolling(int(self.lookback.value)).max()
        )
        # Fast EMA slope (backward-looking).
        dataframe["ema_fast_rising"] = dataframe["ema_fast"] > dataframe["ema_fast"].shift(2)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        uptrend = (
            (dataframe["ema_fast"] > dataframe["ema_slow"])
            & (dataframe["close"] > dataframe["ema_slow"])
            & dataframe["ema_fast_rising"]
        )
        pullback = (
            (dataframe["ema_dist"] >= self.pullback_low.value)
            & (dataframe["ema_dist"] <= self.pullback_high.value)
            & (dataframe["recent_max_dist"] > self.pullback_high.value)
        )
        resting = (
            (dataframe["rsi"] >= self.rsi_low.value)
            & (dataframe["rsi"] <= self.rsi_high.value)
        )
        cond = uptrend & pullback & resting & (dataframe["volume"] > 0)
        dataframe.loc[cond, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (dataframe["close"] < dataframe["ema_slow"]) & (dataframe["volume"] > 0)
        dataframe.loc[cond, "exit_long"] = 1
        return dataframe
