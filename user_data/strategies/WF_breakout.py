# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
import numpy as np
import pandas as pd
from pandas import DataFrame

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.strategy import IStrategy


class WFBreakout(IStrategy):
    """
    Donchian / range breakout on 4h candles (walk-forward candidate).

    Entry:  close breaks above the highest high of the PREVIOUS `dc_entry` candles
            (window shifted by 1 so the current candle is not in its own window),
            with candle volume above the average volume of the previous `vol_period`
            candles.
    Exit:   close drops below the lowest low of the PREVIOUS `dc_exit` candles,
            or a trailing stop once the trade is in profit.
    Stoploss is a hard catastrophic stop; the trailing stop does the real work.
    """

    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI (minutes -> fraction). One 4h candle = 240 minutes.
    # A breakout that has not paid within ~3 days is a dead breakout:
    # bank 10% any time, 4% after 24h, break-even (after fees) after 72h.
    minimal_roi = {
        "0": 0.10,
        "1440": 0.04,
        "4320": 0.0,
    }

    # Hard stop. Breakout fails -> get out.
    stoploss = -0.07

    # Trailing stop: once 5% in profit, trail 3% below the high-water mark.
    trailing_stop = True
    trailing_stop_positive = 0.03
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True

    # Plain round parameters (not hyperopted).
    dc_entry = 20     # breakout window (previous N candles)  -> 80h
    dc_exit = 10      # exit window (previous N candles)      -> 40h
    vol_period = 20   # volume average window
    vol_mult = 1.0    # volume must exceed vol_mult * average

    # Warmup: max(dc_entry, vol_period) + 1 for the shift, with margin. Must stay <= 60.
    startup_candle_count: int = 30

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Highest high of the PREVIOUS dc_entry candles (current candle excluded).
        dataframe["dc_high"] = dataframe["high"].rolling(self.dc_entry).max().shift(1)
        # Lowest low of the PREVIOUS dc_exit candles (current candle excluded).
        dataframe["dc_low"] = dataframe["low"].rolling(self.dc_exit).min().shift(1)
        # Average volume of the PREVIOUS vol_period candles (current candle excluded).
        dataframe["vol_avg"] = dataframe["volume"].rolling(self.vol_period).mean().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["close"] > dataframe["dc_high"])
            & (dataframe["volume"] > dataframe["vol_avg"] * self.vol_mult)
            & (dataframe["volume"] > 0)
            & dataframe["dc_high"].notna()
            & dataframe["vol_avg"].notna()
        )
        dataframe.loc[cond, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["close"] < dataframe["dc_low"])
            & dataframe["dc_low"].notna()
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[cond, "exit_long"] = 1
        return dataframe
