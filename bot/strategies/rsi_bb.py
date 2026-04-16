"""
RSI + Bollinger Bands Stratejisi
- Al sinyali: RSI < 30 VE fiyat alt BB bandının altında
- Sat sinyali: RSI > 70 VE fiyat üst BB bandının üstünde
- Default Timeframe: 15m
"""

from typing import List, Optional

import pandas as pd
import ta

from .base import BaseStrategy, Signal, SignalType


class RsiBbStrategy(BaseStrategy):
    """RSI ve Bollinger Bands birleşik stratejisi"""

    name = "rsi_bb"
    default_timeframe = "15m"
    min_candles = 50

    def __init__(self, config):
        super().__init__(config)
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_oversold = config.get("rsi_oversold", 30)
        self.rsi_overbought = config.get("rsi_overbought", 70)
        self.bb_period = config.get("bb_period", 20)
        self.bb_std = config.get("bb_std", 2.0)

    async def generate_signal(
        self, symbol: str, ohlcv: List[List]
    ) -> Optional[Signal]:
        if len(ohlcv) < self.min_candles:
            return None

        df = self.ohlcv_to_df(ohlcv)

        # RSI
        rsi_ind = ta.momentum.RSIIndicator(df["close"], window=self.rsi_period)
        df["rsi"] = rsi_ind.rsi()

        # Bollinger Bands
        bb = ta.volatility.BollingerBands(
            df["close"], window=self.bb_period, window_dev=self.bb_std
        )
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_middle"] = bb.bollinger_mavg()

        # Son kapanan mum
        last = df.iloc[-1]
        price = float(last["close"])
        rsi = float(last["rsi"]) if not pd.isna(last["rsi"]) else None
        bb_upper = float(last["bb_upper"]) if not pd.isna(last["bb_upper"]) else None
        bb_lower = float(last["bb_lower"]) if not pd.isna(last["bb_lower"]) else None

        if rsi is None or bb_upper is None or bb_lower is None:
            return None

        metadata = {
            "rsi": round(rsi, 2),
            "bb_upper": round(bb_upper, 4),
            "bb_lower": round(bb_lower, 4),
            "price": price,
        }

        # AL sinyali
        if rsi < self.rsi_oversold and price < bb_lower:
            strength = min(
                1.0,
                ((self.rsi_oversold - rsi) / self.rsi_oversold)
                + ((bb_lower - price) / bb_lower if bb_lower else 0),
            )
            signal = Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=price,
                strength=round(strength, 3),
                reason=f"RSI {rsi:.1f} < {self.rsi_oversold} ve fiyat alt BB altinda",
                metadata=metadata,
            )
            self.record_signal(signal)
            return signal

        # SAT sinyali
        if rsi > self.rsi_overbought and price > bb_upper:
            strength = min(
                1.0,
                ((rsi - self.rsi_overbought) / (100 - self.rsi_overbought))
                + ((price - bb_upper) / bb_upper if bb_upper else 0),
            )
            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=price,
                strength=round(strength, 3),
                reason=f"RSI {rsi:.1f} > {self.rsi_overbought} ve fiyat ust BB uzerinde",
                metadata=metadata,
            )
            self.record_signal(signal)
            return signal

        # HOLD
        signal = Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            strength=0.0,
            reason=f"RSI: {rsi:.1f} (notr)",
            metadata=metadata,
        )
        self.record_signal(signal)
        return signal
