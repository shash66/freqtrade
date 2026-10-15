# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
import numpy as np
import pandas as pd
from pandas import DataFrame

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.strategy import IStrategy, IntParameter


class WFRsiRegime(IStrategy):
    """
    4h regime-filtered dip buying.

    Buy an RSI(14) oversold dip, but ONLY while price sits above a slow EMA,
    i.e. buy pullbacks inside an uptrend and refuse to catch falling knives in
    a downtrend. The regime filter is the whole point of this candidate.

    Entry:  RSI(14) < rsi_buy  AND  close > EMA(ema_len)
    Exit:   RSI(14) > rsi_exit (bounce is done), or the ROI table, or the stoploss.

    Periods are adapted to 4h candles: EMA(60) ~ 10 days of regime context,
    which is the longest the 60-candle startup limit allows.
    """

    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI targets (minutes -> net profit fraction). One 4h candle = 240 minutes.
    # A dip bounce inside a 4h uptrend usually resolves within 1-3 days;
    # bank a good move early, accept less as the trade ages.
    minimal_roi = {
        "0": 0.06,      # any time: +6%
        "1440": 0.03,   # after 1 day (6 candles): +3%
        "2880": 0.015,  # after 2 days (12 candles): +1.5%
    }

    # If a dip in an "uptrend" runs 8% against us, the uptrend premise is wrong.
    stoploss = -0.08

    trailing_stop = False

    # Warmup for the EMA(60) regime line + RSI(14). Hard limit is 60.
    startup_candle_count: int = 60

    # Parameters (plain round numbers; not hyperopted).
    # In-sample on 4h, RSI(14) < 35 with close > EMA(50) fired on zero candles
    # across all six pairs, so both knobs sit at the loose end of the allowed
    # range (RSI < 40, EMA 60). Even that is a very rare signal on 4h.
    rsi_len = IntParameter(10, 20, default=14, space="buy", optimize=False)
    rsi_buy = IntParameter(25, 45, default=40, space="buy", optimize=False)
    ema_len = IntParameter(30, 60, default=60, space="buy", optimize=False)
    rsi_exit = IntParameter(60, 80, default=65, space="sell", optimize=False)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=int(self.rsi_len.value))
        dataframe["ema_regime"] = ta.EMA(dataframe, timeperiod=int(self.ema_len.value))
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["rsi"] < self.rsi_buy.value)  # oversold dip
            & (dataframe["close"] > dataframe["ema_regime"])  # ...but still in an uptrend
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[cond, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["rsi"] > self.rsi_exit.value)  # bounce exhausted
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[cond, "exit_long"] = 1
        return dataframe
