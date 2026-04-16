"""
Grid Trading Stratejisi
Belirlenen fiyat aralığında eşit seviyelerde alış/satış emirleri oluşturur.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseStrategy, Signal, SignalType

logger = logging.getLogger(__name__)


class GridTradingStrategy(BaseStrategy):
    """Grid trading stratejisi"""

    name = "grid_trading"
    default_timeframe = "5m"
    min_candles = 20

    def __init__(self, config):
        super().__init__(config)
        self.lower_price_pct = config.get("lower_price_pct", 0.95)
        self.upper_price_pct = config.get("upper_price_pct", 1.05)
        self.grid_count = config.get("grid_count", 10)
        self.amount_per_grid = config.get("amount_per_grid", 10.0)
        # Sembol başına grid durumu
        self.grids: Dict[str, Dict[str, Any]] = {}

    def _initialize_grid(self, symbol: str, current_price: float):
        """Belirli bir sembol için grid seviyelerini hesapla"""
        lower = current_price * self.lower_price_pct
        upper = current_price * self.upper_price_pct
        step = (upper - lower) / (self.grid_count - 1) if self.grid_count > 1 else 0

        levels = []
        for i in range(self.grid_count):
            price = lower + step * i
            # Alt seviyeler -> al, üst seviyeler -> sat
            side = "buy" if price < current_price else "sell"
            levels.append(
                {
                    "index": i,
                    "price": round(price, 6),
                    "side": side,
                    "triggered": False,
                }
            )

        self.grids[symbol] = {
            "reference_price": current_price,
            "lower": lower,
            "upper": upper,
            "step": step,
            "levels": levels,
        }
        logger.info(
            f"Grid olusturuldu: {symbol} [{lower:.4f} - {upper:.4f}] "
            f"{self.grid_count} seviye"
        )

    async def generate_signal(
        self, symbol: str, ohlcv: List[List]
    ) -> Optional[Signal]:
        if len(ohlcv) < self.min_candles:
            return None

        df = self.ohlcv_to_df(ohlcv)
        price = float(df.iloc[-1]["close"])

        # Grid henüz yoksa oluştur
        if symbol not in self.grids:
            self._initialize_grid(symbol, price)
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=price,
                reason="Grid baslatildi",
                metadata=self.grids[symbol],
            )

        grid = self.grids[symbol]

        # Sınırların dışına çıktıysa gridi yeniden konumla
        if price < grid["lower"] * 0.98 or price > grid["upper"] * 1.02:
            self._initialize_grid(symbol, price)
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=price,
                reason="Fiyat grid disinda, yeniden konumlanildi",
            )

        # Fiyat bir grid seviyesine değdi mi?
        tolerance = grid["step"] * 0.1 if grid["step"] > 0 else price * 0.001

        for level in grid["levels"]:
            if abs(price - level["price"]) <= tolerance and not level["triggered"]:
                level["triggered"] = True
                sig_type = (
                    SignalType.BUY if level["side"] == "buy" else SignalType.SELL
                )
                signal = Signal(
                    type=sig_type,
                    symbol=symbol,
                    price=price,
                    strength=0.8,
                    reason=(
                        f"Grid seviyesi tetiklendi: #{level['index']} @ "
                        f"{level['price']:.4f}"
                    ),
                    metadata={
                        "grid_level": level["index"],
                        "grid_price": level["price"],
                        "amount_per_grid": self.amount_per_grid,
                    },
                )
                self.record_signal(signal)
                return signal

        # Triggered seviyeleri yeterince uzaklaşılınca reset et
        for level in grid["levels"]:
            if level["triggered"]:
                distance = abs(price - level["price"])
                if distance > grid["step"] * 0.5:
                    level["triggered"] = False

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            reason="Grid seviyesi yok",
        )
