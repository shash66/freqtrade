# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
import numpy as np
import pandas as pd
from pandas import DataFrame

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.strategy import IStrategy, IntParameter


class WFMacdMomentum(IStrategy):
    """
    4h momentum candidate: MACD histogram flips positive while price sits above EMA(40).

    Entry:  MACD(12,26,9) histogram crosses from <= 0 to > 0 (fresh bullish momentum)
            AND close > EMA(40) (only trade with the ~1-week trend)
            AND ADX(14) < 20 (trend not yet established: catch the ignition,
            do not chase a flip inside a mature trend, which on 4h tends to be
            the last leg).
    Exit:   MACD histogram crosses from >= 0 to < 0 (momentum gone), or ROI target hit.
    Stoploss is moderately wide so a multi-day 4h momentum move has room to develop.

    Note: the 1h version gated on ADX > 20. On 4h in-sample every flip taken
    with ADX >= 20 lost, and every ROI winner came from ADX < 20, so the gate
    is inverted here. Treat that as a hypothesis to be checked on held-out data.

    No lookahead: all comparisons use the current row vs the previous row (shift(1)).
    """

    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI targets (minutes -> fraction). One 4h candle = 240 minutes.
    # Bank +3% whenever it is reached: fewer than half of the flips ever see
    # +3% before the histogram turns back, so waiting for more gives it back.
    # After 3 days the trade is stale; take +2%. Fees are 0.8% round trip.
    minimal_roi = {
        "0": 0.03,      # any time: +3%
        "4320": 0.02,   # after 3 days (18 candles): +2%
    }

    stoploss = -0.06

    trailing_stop = False

    # EMA(40) and MACD(26, 9) need warmup. Hard limit is 60.
    startup_candle_count: int = 60

    # Parameters (plain round values; not hyperopted).
    macd_fast = IntParameter(8, 16, default=12, space="buy", optimize=False)
    macd_slow = IntParameter(20, 32, default=26, space="buy", optimize=False)
    macd_signal = IntParameter(5, 12, default=9, space="buy", optimize=False)
    ema_trend = IntParameter(30, 50, default=40, space="buy", optimize=False)
    adx_period = IntParameter(10, 20, default=14, space="buy", optimize=False)
    adx_threshold = IntParameter(15, 30, default=20, space="buy", optimize=False)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        macd = ta.MACD(
            dataframe,
            fastperiod=int(self.macd_fast.value),
            slowperiod=int(self.macd_slow.value),
            signalperiod=int(self.macd_signal.value),
        )
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]
        dataframe["macdhist_prev"] = dataframe["macdhist"].shift(1)

        dataframe["ema_trend"] = ta.EMA(dataframe, timeperiod=int(self.ema_trend.value))
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=int(self.adx_period.value))
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["macdhist"] > 0)
            & (dataframe["macdhist_prev"] <= 0)
            & (dataframe["close"] > dataframe["ema_trend"])
            & (dataframe["adx"] < self.adx_threshold.value)
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[cond, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["macdhist"] < 0)
            & (dataframe["macdhist_prev"] >= 0)
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[cond, "exit_long"] = 1
        return dataframe
