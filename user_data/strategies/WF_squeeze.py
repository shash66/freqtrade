# --- Walk-forward candidate: volatility squeeze breakout on 4h (Bollinger inside Keltner) ---
from datetime import datetime

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.strategy import IStrategy, stoploss_from_open


class WFSqueeze(IStrategy):
    """
    Volatility squeeze breakout on 4h candles.

    Squeeze = Bollinger Bands (20, 2) sitting fully inside the Keltner Channel
    (EMA 20 +/- 1.5 * ATR 20) for at least `min_squeeze` consecutive candles.
    Entry when the squeeze releases (bands expand back outside the channel) and
    the release is upward: close above the upper Bollinger band, TTM-style
    momentum positive, close above the Keltner mid-line.
    Exit when momentum rolls over (negative, or falling two candles in a row) or ROI.
    Stoploss is ATR based (3 * ATR% of the signal candle, clipped to 4%..8%),
    with a hard static floor of -10%.
    """

    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    # BB/KC/LINEARREG all use 20 candles; LINEARREG sits on a 20-candle rolling mid,
    # so ~40 candles are needed. 50 leaves margin and stays under the 60 limit.
    startup_candle_count = 50

    # ROI (minutes -> fraction). One 4h candle = 240 minutes.
    # Breakouts should pay within a few candles; decay toward break-even
    # (fees are 0.8% round trip) over ~2 days.
    minimal_roi = {
        "0": 0.06,
        "720": 0.04,
        "1440": 0.025,
        "2880": 0.01,
    }

    # Hard floor; the ATR stop below is tighter in practice.
    stoploss = -0.10
    use_custom_stoploss = True
    trailing_stop = False

    # --- parameters (round, not hyperopted) ---
    bb_period = 20
    bb_std = 2.0
    kc_period = 20
    kc_mult = 1.5
    min_squeeze = 4        # consecutive squeeze candles required (4 x 4h = 16h)
    release_window = 2     # entry allowed on the release candle or the one after
    atr_period = 14
    # stop = clip(atr_stop_mult * ATR%, atr_stop_min, atr_stop_max)
    atr_stop_mult = 3.0
    atr_stop_min = 0.04
    atr_stop_max = 0.08

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    # ------------------------------------------------------------------ indicators
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Bollinger Bands (20, 2) on typical price
        bb = qtpylib.bollinger_bands(
            qtpylib.typical_price(dataframe), window=self.bb_period, stds=self.bb_std
        )
        dataframe["bb_lower"] = bb["lower"]
        dataframe["bb_mid"] = bb["mid"]
        dataframe["bb_upper"] = bb["upper"]

        # Keltner Channel (EMA 20, 1.5 * ATR 20)
        dataframe["kc_mid"] = ta.EMA(dataframe, timeperiod=self.kc_period)
        dataframe["atr_kc"] = ta.ATR(dataframe, timeperiod=self.kc_period)
        dataframe["kc_upper"] = dataframe["kc_mid"] + self.kc_mult * dataframe["atr_kc"]
        dataframe["kc_lower"] = dataframe["kc_mid"] - self.kc_mult * dataframe["atr_kc"]

        # ATR for the stoploss
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]

        # Squeeze on: BB entirely inside KC
        dataframe["squeeze_on"] = (
            (dataframe["bb_upper"] < dataframe["kc_upper"])
            & (dataframe["bb_lower"] > dataframe["kc_lower"])
        ).astype(int)

        # consecutive squeeze candle count (resets to 0 when squeeze is off)
        sq = dataframe["squeeze_on"]
        grp = (sq != sq.shift(1)).cumsum()
        dataframe["squeeze_count"] = sq.groupby(grp).cumsum() * sq

        # release candle: squeeze is off now, was on (long enough) on the previous candle
        dataframe["release"] = (
            (dataframe["squeeze_on"] == 0)
            & (dataframe["squeeze_count"].shift(1) >= self.min_squeeze)
        ).astype(int)
        # recent release: a release happened on this candle or within release_window - 1 before
        dataframe["release_recent"] = (
            dataframe["release"].rolling(self.release_window, min_periods=1).max()
        )

        # TTM-style momentum: linear regression of close minus (Donchian mid + SMA)/2
        hh = dataframe["high"].rolling(self.bb_period).max()
        ll = dataframe["low"].rolling(self.bb_period).min()
        sma = ta.SMA(dataframe, timeperiod=self.bb_period)
        mid = ((hh + ll) / 2.0 + sma) / 2.0
        delta = dataframe["close"] - mid
        dataframe["mom"] = ta.LINEARREG(delta, timeperiod=self.bb_period)
        dataframe["mom_prev"] = dataframe["mom"].shift(1)
        dataframe["mom_prev2"] = dataframe["mom"].shift(2)

        return dataframe

    # ------------------------------------------------------------------ entry
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # upward release: close above the upper Bollinger band AND momentum positive
        breakout = dataframe["close"] > dataframe["bb_upper"]
        mom_pos = dataframe["mom"] > 0

        cond = (
            (dataframe["release_recent"] > 0)
            & (dataframe["squeeze_on"] == 0)
            & breakout
            & mom_pos
            & (dataframe["close"] > dataframe["kc_mid"])
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[cond, "enter_long"] = 1
        return dataframe

    # ------------------------------------------------------------------ exit
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # momentum has turned: negative, or falling two candles in a row
        mom_negative = dataframe["mom"] < 0
        mom_falling = (dataframe["mom"] < dataframe["mom_prev"]) & (
            dataframe["mom_prev"] < dataframe["mom_prev2"]
        )

        cond = (mom_negative | mom_falling) & (dataframe["volume"] > 0)
        dataframe.loc[cond, "exit_long"] = 1
        return dataframe

    # ------------------------------------------------------------------ ATR stoploss
    def custom_stoploss(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ):
        """
        Fixed stop anchored to the open price at (atr_stop_mult * ATR%) of the signal
        candle (the last fully closed candle before the trade opened), clipped to
        [atr_stop_min, atr_stop_max]. The static `stoploss` stays the hard floor.
        Only candles dated before the trade open are used, so no lookahead.
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None
        signal_rows = dataframe.loc[dataframe["date"] < trade.open_date_utc]
        if signal_rows.empty:
            return None
        atr_pct = signal_rows["atr_pct"].iloc[-1]
        if atr_pct is None or np.isnan(atr_pct):
            return None
        stop_pct = float(np.clip(self.atr_stop_mult * atr_pct, self.atr_stop_min, self.atr_stop_max))
        return stoploss_from_open(
            -stop_pct, current_profit, is_short=trade.is_short, leverage=trade.leverage
        )
