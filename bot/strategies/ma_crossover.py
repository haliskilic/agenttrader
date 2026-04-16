"""
EMA Crossover Stratejisi
- Al: EMA9 EMA21'in üzerine geçtiğinde (golden cross)
- Sat: EMA9 EMA21'in altına geçtiğinde (death cross)
- Default Timeframe: 1h
"""

from typing import List, Optional

import pandas as pd
import ta

from .base import BaseStrategy, Signal, SignalType


class MaCrossoverStrategy(BaseStrategy):
    """EMA Crossover stratejisi"""

    name = "ma_crossover"
    default_timeframe = "1h"
    min_candles = 50

    def __init__(self, config):
        super().__init__(config)
        self.ema_fast = config.get("ema_fast", 9)
        self.ema_slow = config.get("ema_slow", 21)

    async def generate_signal(
        self, symbol: str, ohlcv: List[List]
    ) -> Optional[Signal]:
        if len(ohlcv) < self.min_candles:
            return None

        df = self.ohlcv_to_df(ohlcv)

        df["ema_fast"] = ta.trend.EMAIndicator(
            df["close"], window=self.ema_fast
        ).ema_indicator()
        df["ema_slow"] = ta.trend.EMAIndicator(
            df["close"], window=self.ema_slow
        ).ema_indicator()

        last = df.iloc[-1]
        prev = df.iloc[-2]

        if (
            pd.isna(last["ema_fast"])
            or pd.isna(last["ema_slow"])
            or pd.isna(prev["ema_fast"])
            or pd.isna(prev["ema_slow"])
        ):
            return None

        price = float(last["close"])
        fast_now = float(last["ema_fast"])
        slow_now = float(last["ema_slow"])
        fast_prev = float(prev["ema_fast"])
        slow_prev = float(prev["ema_slow"])

        metadata = {
            "ema_fast": round(fast_now, 4),
            "ema_slow": round(slow_now, 4),
            "price": price,
        }

        # Golden cross (bullish) - önceki mumda kesin olarak altında olmalı
        # (eşitlikte crossover saymıyoruz, aksi halde "düz seyreden" piyasada
        # yanlış sinyal üretebilir)
        if fast_prev < slow_prev and fast_now > slow_now:
            strength = min(1.0, abs(fast_now - slow_now) / slow_now * 10)
            signal = Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=price,
                strength=round(strength, 3),
                reason=f"Golden cross: EMA{self.ema_fast} > EMA{self.ema_slow}",
                metadata=metadata,
            )
            self.record_signal(signal)
            return signal

        # Death cross (bearish) - önceki mumda kesin olarak üstünde olmalı
        if fast_prev > slow_prev and fast_now < slow_now:
            strength = min(1.0, abs(fast_now - slow_now) / slow_now * 10)
            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=price,
                strength=round(strength, 3),
                reason=f"Death cross: EMA{self.ema_fast} < EMA{self.ema_slow}",
                metadata=metadata,
            )
            self.record_signal(signal)
            return signal

        # HOLD
        trend = "yukari" if fast_now > slow_now else "asagi"
        signal = Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            strength=0.0,
            reason=f"Crossover yok, trend: {trend}",
            metadata=metadata,
        )
        self.record_signal(signal)
        return signal
