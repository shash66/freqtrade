# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
import numpy as np
import pandas as pd
from pandas import DataFrame

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.strategy import IStrategy, IntParameter


class WFEmaTrend(IStrategy):
    """
    4h trend-following candidate: fast/slow EMA crossover gated by an ADX trend-strength filter.

    Entry:  fast EMA crosses above slow EMA while ADX > adx_threshold (trend confirmed).
    Exit:   fast EMA crosses below slow EMA (trend over), plus ROI targets.
    Stoploss is moderately wide so a 4h trend has room to breathe.

    Periods are adapted to 4h candles (8/21 EMAs ~ 1.3 / 3.5 days).
    ADX threshold is 25 (not 20): on 4h the looser gate let in too many
    weak-trend entries that reversed within two days.
    """

    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI targets (minutes -> fraction). One 4h candle = 240 minutes.
    # Let the trend run, but bank a fat move; lower the bar as the trade ages.
    minimal_roi = {
        "0": 0.10,      # any time: +10%
        "1440": 0.05,   # after 1 day (6 candles): +5%
        "4320": 0.025,  # after 3 days (18 candles): +2.5%
    }

    # Moderately wide stoploss so a 4h trend has room.
    stoploss = -0.08

    trailing_stop = False

    # Warmup for slow EMA(21) + ADX(14). Hard limit is 60.
    startup_candle_count: int = 60

    # Parameters (plain round numbers; not hyperopted).
    ema_fast = IntParameter(5, 15, default=8, space="buy", optimize=False)
    ema_slow = IntParameter(15, 40, default=21, space="buy", optimize=False)
    adx_period = IntParameter(10, 20, default=14, space="buy", optimize=False)
    adx_threshold = IntParameter(15, 35, default=25, space="buy", optimize=False)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=int(self.ema_fast.value))
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=int(self.ema_slow.value))
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=int(self.adx_period.value))
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            qtpylib.crossed_above(dataframe["ema_fast"], dataframe["ema_slow"])
            & (dataframe["adx"] > self.adx_threshold.value)
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[cond, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            qtpylib.crossed_below(dataframe["ema_fast"], dataframe["ema_slow"])
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[cond, "exit_long"] = 1
        return dataframe
