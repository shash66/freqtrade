# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
import numpy as np
import pandas as pd
from pandas import DataFrame

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter


class WFBbMeanRev(IStrategy):
    """
    Mean reversion on 4h candles: Bollinger band (20, 2.0) + RSI(14) oversold.

    Entry:  close below the lower Bollinger band AND RSI(14) < rsi_entry.
    Exit:   close back above the middle band (20 SMA) OR RSI(14) > rsi_exit.
    Tight stoploss: a mean-reversion entry that keeps falling is simply wrong.

    All indicators use only past data (standard trailing rolling windows and
    Wilder RSI). No shift(-n), no centered windows, no repainting.
    """

    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI targets (minutes -> fraction). One 4h candle = 240 minutes.
    # Bank a strong bounce immediately; accept a smaller one as the trade ages.
    minimal_roi = {
        "0": 0.06,
        "480": 0.03,
        "1440": 0.015,
    }

    # Tight stoploss (must stay within -0.03 .. -0.15).
    stoploss = -0.05

    trailing_stop = False

    # Warmup for BB(20) and RSI(14) (Wilder smoothing needs extra candles). Hard limit 60.
    startup_candle_count: int = 40

    # Parameters (plain round numbers; not hyperopted).
    bb_window = IntParameter(10, 30, default=20, space="buy", optimize=False)
    bb_std = DecimalParameter(1.5, 3.0, default=2.0, decimals=1, space="buy", optimize=False)
    rsi_period = IntParameter(10, 20, default=14, space="buy", optimize=False)
    rsi_entry = IntParameter(20, 40, default=35, space="buy", optimize=False)
    rsi_exit = IntParameter(50, 70, default=60, space="sell", optimize=False)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        bollinger = qtpylib.bollinger_bands(
            dataframe["close"],
            window=int(self.bb_window.value),
            stds=float(self.bb_std.value),
        )
        dataframe["bb_lower"] = bollinger["lower"]
        dataframe["bb_middle"] = bollinger["mid"]
        dataframe["bb_upper"] = bollinger["upper"]
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=int(self.rsi_period.value))
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["close"] < dataframe["bb_lower"])
            & (dataframe["rsi"] < int(self.rsi_entry.value))
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[cond, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["close"] > dataframe["bb_middle"])
            | (dataframe["rsi"] > int(self.rsi_exit.value))
        ) & (dataframe["volume"] > 0)
        dataframe.loc[cond, "exit_long"] = 1
        return dataframe
