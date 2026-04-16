"""
Stochastic RSI Strategy
- StochRSI(14,14,3,3)
- Volume filtresi: hacim 20-periyot SMA'nın üzerinde olmalı
- Timeframe: 30m
"""

from typing import Dict, List, Optional

import pandas as pd
from ta.momentum import StochRSIIndicator

from .base import BaseStrategy, Signal, SignalType


class StochRsiStrategy(BaseStrategy):
    """Stochastic RSI + Volume filtreli strateji.

    Sinyal Mantığı
    --------------
    BUY: %K 20 bölgesinden yukarı çıktı (prev < 20, now > 20) VE
         hacim > SMA20(hacim) * volume_mult.
    SELL: %K 80 bölgesinden aşağı düştü (prev > 80, now < 80) VE
          hacim > SMA20(hacim) * volume_mult.

    Cooldown
    --------
    Aynı sembol için son sinyalden sonra `cooldown_bars` (default 3) mum
    geçmeden yeni sinyal üretmez. Bu, yatay seyreden piyasada çift crossover
    ile gelen whipsaw'ları filtreler.
    """

    name = "stoch_rsi"
    default_timeframe = "30m"
    min_candles = 60  # RSI(14) + Stoch(14) + smooth(3) için yeterli

    def __init__(self, config):
        super().__init__(config)
        self.rsi_period = config.get("rsi_period", 14)
        self.stoch_period = config.get("stoch_period", 14)
        self.k_smooth = config.get("k_smooth", 3)
        self.d_smooth = config.get("d_smooth", 3)
        self.volume_mult = config.get("volume_mult", 1.2)
        self.volume_sma_period = config.get("volume_sma_period", 20)
        self.oversold_level = config.get("oversold_level", 0.2)  # %20 (0-1)
        self.overbought_level = config.get("overbought_level", 0.8)
        self.cooldown_bars = config.get("cooldown_bars", 3)
        self.stop_loss_pct = config.get("stop_loss_pct", 0.018)
        self.take_profit_pct = config.get("take_profit_pct", 0.036)
        # Son sinyalin üretildiği mum sayısı (symbol -> bar_index).
        # 0 = daha hiç sinyal üretilmemiş (cooldown geçerli değil).
        self._last_signal_bar: Dict[str, int] = {}

    async def generate_signal(
        self, symbol: str, ohlcv: List[List]
    ) -> Optional[Signal]:
        if len(ohlcv) < self.min_candles:
            return None

        df = self.ohlcv_to_df(ohlcv)

        # StochRSI
        stoch_rsi = StochRSIIndicator(
            df["close"],
            window=self.stoch_period,
            smooth1=self.k_smooth,
            smooth2=self.d_smooth,
        )
        df["stoch_k"] = stoch_rsi.stochrsi_k()
        df["stoch_d"] = stoch_rsi.stochrsi_d()

        # Hacim SMA
        df["vol_sma"] = df["volume"].rolling(window=self.volume_sma_period).mean()

        last = df.iloc[-1]
        prev = df.iloc[-2]

        required_cols = ["stoch_k", "stoch_d", "vol_sma"]
        if any(pd.isna(last[c]) or pd.isna(prev[c]) for c in required_cols):
            return None

        price = float(last["close"])
        k_now = float(last["stoch_k"])
        k_prev = float(prev["stoch_k"])
        d_now = float(last["stoch_d"])
        vol_now = float(last["volume"])
        vol_sma = float(last["vol_sma"])

        # Cooldown kontrolü - bar index olarak mevcut OHLCV uzunluğu kullanılır.
        # Böylece canlı ve backtest her ikisinde tutarlı çalışır.
        current_bar = len(ohlcv)
        last_sig_bar = self._last_signal_bar.get(symbol, 0)
        in_cooldown = (current_bar - last_sig_bar) < self.cooldown_bars and last_sig_bar > 0

        metadata = {
            "stoch_k": round(k_now, 4),
            "stoch_d": round(d_now, 4),
            "volume": round(vol_now, 4),
            "vol_sma": round(vol_sma, 4),
            "price": price,
        }

        volume_ok = vol_sma > 0 and vol_now > (vol_sma * self.volume_mult)

        # BUY: %K 20 altından yukarı çıktı
        buy_cross = k_prev < self.oversold_level and k_now > self.oversold_level
        if buy_cross and volume_ok and not in_cooldown:
            self._last_signal_bar[symbol] = current_bar
            strength = min(1.0, (vol_now / max(vol_sma, 1e-9)) / 3.0 + 0.4)
            signal = Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=price,
                strength=round(strength, 3),
                reason=(
                    f"StochRSI %K oversold-cikis ({k_prev*100:.1f} -> {k_now*100:.1f}), "
                    f"hacim {vol_now:.2f} > SMA20*{self.volume_mult} ({vol_sma*self.volume_mult:.2f})"
                ),
                metadata=metadata,
            )
            self.record_signal(signal)
            return signal

        # SELL: %K 80 üzerinden aşağı düştü
        sell_cross = k_prev > self.overbought_level and k_now < self.overbought_level
        if sell_cross and volume_ok and not in_cooldown:
            self._last_signal_bar[symbol] = current_bar
            strength = min(1.0, (vol_now / max(vol_sma, 1e-9)) / 3.0 + 0.4)
            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=price,
                strength=round(strength, 3),
                reason=(
                    f"StochRSI %K overbought-dusus ({k_prev*100:.1f} -> {k_now*100:.1f}), "
                    f"hacim {vol_now:.2f} > SMA20*{self.volume_mult} ({vol_sma*self.volume_mult:.2f})"
                ),
                metadata=metadata,
            )
            self.record_signal(signal)
            return signal

        # HOLD
        reason = f"StochRSI %K: {k_now*100:.1f} (cooldown: {in_cooldown})"
        signal = Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            strength=0.0,
            reason=reason,
            metadata=metadata,
        )
        self.record_signal(signal)
        return signal
