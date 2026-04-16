"""Trading Stratejileri"""

from .base import BaseStrategy, Signal, SignalType
from .rsi_bb import RsiBbStrategy
from .ma_crossover import MaCrossoverStrategy
from .grid_trading import GridTradingStrategy
from .macd_signal import MacdSignalStrategy
from .stoch_rsi import StochRsiStrategy

STRATEGY_REGISTRY = {
    "rsi_bb": RsiBbStrategy,
    "ma_crossover": MaCrossoverStrategy,
    "grid_trading": GridTradingStrategy,
    "macd_signal": MacdSignalStrategy,
    "stoch_rsi": StochRsiStrategy,
}


def get_strategy(name: str) -> type:
    """Strateji sınıfını isme göre al"""
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"Bilinmeyen strateji: {name}")
    return STRATEGY_REGISTRY[name]


__all__ = [
    "BaseStrategy",
    "Signal",
    "SignalType",
    "RsiBbStrategy",
    "MaCrossoverStrategy",
    "GridTradingStrategy",
    "MacdSignalStrategy",
    "StochRsiStrategy",
    "STRATEGY_REGISTRY",
    "get_strategy",
]
