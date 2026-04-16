"""
MACD Signal Strategy
- MACD(12,26,9) crossover
- EMA200 trend filtresi: sadece trend yönünde işlem
- Timeframe: 1h
"""

from typing import List, Optional

import pandas as pd
from ta.trend import MACD, EMAIndicator

from .base import BaseStrategy, Signal, SignalType


class MacdSignalStrategy(BaseStrategy):
    """MACD + EMA200 trend filtresi stratejisi.

    Sinyal Mantığı
    --------------
    BUY: MACD histogram pozitife döndü (yeni crossover: MACD > Signal, önceki
         barda değildi) VE fiyat EMA200'ün üzerinde (uptrend onayı).
    SELL: MACD histogram negatife döndü (yeni crossover: MACD < Signal, önceki
          barda değildi) VE fiyat EMA200 altında (downtrend onayı).

    Güç (strength)
    --------------
    MACD çizgisi sıfırın altındayken oluşan BUY (dip-reversal) = 1.0
    MACD çizgisi sıfırın üstündeyken oluşan BUY (trend devam) = 0.7
    SELL için simetrik mantık.
    """

    name = "macd_signal"
    default_timeframe = "1h"
    min_candles = 230  # EMA200 + MACD stabilization için tampon

    def __init__(self, config):
        super().__init__(config)
        self.fast = config.get("fast", 12)
        self.slow = config.get("slow", 26)
        self.signal_period = config.get("signal_period", 9)
        self.ema_trend_period = config.get("ema_trend_period", 200)
        self.stop_loss_pct = config.get("stop_loss_pct", 0.025)
        self.take_profit_pct = config.get("take_profit_pct", 0.05)

    async def generate_signal(
        self, symbol: str, ohlcv: List[List]
    ) -> Optional[Signal]:
        if len(ohlcv) < self.min_candles:
            return None

        df = self.ohlcv_to_df(ohlcv)

        # MACD
        macd_ind = MACD(
            df["close"],
            window_slow=self.slow,
            window_fast=self.fast,
            window_sign=self.signal_period,
        )
        df["macd"] = macd_ind.macd()
        df["macd_signal"] = macd_ind.macd_signal()
        df["macd_diff"] = macd_ind.macd_diff()  # histogram

        # EMA trend filtresi
        ema_trend = EMAIndicator(df["close"], window=self.ema_trend_period).ema_indicator()
        df["ema_trend"] = ema_trend

        last = df.iloc[-1]
        prev = df.iloc[-2]

        required_cols = ["macd", "macd_signal", "macd_diff", "ema_trend"]
        if any(pd.isna(last[c]) or pd.isna(prev[c]) for c in required_cols):
            return None

        price = float(last["close"])
        macd_now = float(last["macd"])
        sig_now = float(last["macd_signal"])
        macd_prev = float(prev["macd"])
        sig_prev = float(prev["macd_signal"])
        ema_trend_val = float(last["ema_trend"])
        hist_now = float(last["macd_diff"])
        hist_prev = float(prev["macd_diff"])

        metadata = {
            "macd": round(macd_now, 6),
            "macd_signal": round(sig_now, 6),
            "macd_hist": round(hist_now, 6),
            "ema_trend": round(ema_trend_val, 4),
            "price": price,
        }

        # Crossover tespit: histogram işaret değişimi veya MACD/signal çaprazlaması
        bullish_cross = macd_prev <= sig_prev and macd_now > sig_now
        bearish_cross = macd_prev >= sig_prev and macd_now < sig_now

        # BUY
        if bullish_cross and price > ema_trend_val:
            # MACD < 0 ise dip-reversal (daha güçlü), MACD > 0 ise trend devam
            strength = 1.0 if macd_now < 0 else 0.7
            signal = Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=price,
                strength=strength,
                reason=(
                    f"MACD bullish crossover ({macd_now:.4f} > {sig_now:.4f}), "
                    f"fiyat EMA{self.ema_trend_period} uzerinde ({price:.2f} > {ema_trend_val:.2f})"
                ),
                metadata=metadata,
            )
            self.record_signal(signal)
            return signal

        # SELL
        if bearish_cross and price < ema_trend_val:
            strength = 1.0 if macd_now > 0 else 0.7
            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=price,
                strength=strength,
                reason=(
                    f"MACD bearish crossover ({macd_now:.4f} < {sig_now:.4f}), "
                    f"fiyat EMA{self.ema_trend_period} altinda ({price:.2f} < {ema_trend_val:.2f})"
                ),
                metadata=metadata,
            )
            self.record_signal(signal)
            return signal

        # HOLD
        trend_dir = "uptrend" if price > ema_trend_val else "downtrend"
        signal = Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            strength=0.0,
            reason=f"Crossover yok, {trend_dir} (hist: {hist_now:.4f})",
            metadata=metadata,
        )
        self.record_signal(signal)
        return signal
