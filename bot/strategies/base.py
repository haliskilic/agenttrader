"""
Strateji Base Class
Tüm stratejiler bu sınıftan türetilir.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    """Python 3.12+ uyumlu UTC timestamp (datetime.utcnow() deprecate edildi).
    Naive döndürür - dataclass default_factory için uyumluluk."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SignalType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"


@dataclass
class Signal:
    """Strateji tarafından üretilen ticaret sinyali"""
    type: SignalType
    symbol: str
    price: float
    timestamp: datetime = field(default_factory=_utcnow_naive)
    strength: float = 1.0  # 0-1 arası güven
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "symbol": self.symbol,
            "price": self.price,
            "timestamp": self.timestamp.isoformat(),
            "strength": self.strength,
            "reason": self.reason,
            "metadata": self.metadata,
        }


class BaseStrategy(ABC):
    """Trading stratejileri için base class"""

    name: str = "base"
    default_timeframe: str = "15m"
    min_candles: int = 50  # Minimum gerekli mum sayısı

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.enabled = config.get("enabled", False)
        self.timeframe = config.get("timeframe", self.default_timeframe)
        self.symbols = config.get("symbols", [])
        self.last_signals: Dict[str, Signal] = {}  # symbol -> last signal
        self.logger = logging.getLogger(f"strategy.{self.name}")

    @abstractmethod
    async def generate_signal(
        self, symbol: str, ohlcv: List[List]
    ) -> Optional[Signal]:
        """
        OHLCV verisinden bir sinyal üret.
        ohlcv: [[timestamp_ms, open, high, low, close, volume], ...]
        """
        raise NotImplementedError

    @staticmethod
    def ohlcv_to_df(ohlcv: List[List]) -> pd.DataFrame:
        """OHLCV listesini DataFrame'e çevir"""
        df = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    def toggle(self, enabled: Optional[bool] = None) -> bool:
        """Stratejiyi aç/kapat"""
        self.enabled = not self.enabled if enabled is None else enabled
        logger.info(f"Strateji '{self.name}' -> {'AKTIF' if self.enabled else 'PASIF'}")
        return self.enabled

    def get_info(self) -> Dict[str, Any]:
        """Strateji bilgilerini döndür"""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "timeframe": self.timeframe,
            "symbols": self.symbols,
            "config": self.config,
            "last_signals": {k: v.to_dict() for k, v in self.last_signals.items()},
        }

    def record_signal(self, signal: Signal):
        """Son sinyali kaydet"""
        self.last_signals[signal.symbol] = signal
