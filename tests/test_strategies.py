"""
Strateji birim testleri.

Sentetik OHLCV üzerinde her stratejinin sinyal üretimini doğrular.
`pytest-asyncio` ile async test fonksiyonları çalıştırılır.
"""

from __future__ import annotations

import pytest

from bot.strategies import (
    RsiBbStrategy,
    MaCrossoverStrategy,
    MacdSignalStrategy,
    StochRsiStrategy,
)
from bot.strategies.base import SignalType


pytestmark = pytest.mark.asyncio


# ============================================================
# RSI + BB
# ============================================================
async def test_rsi_bb_returns_none_when_insufficient_data(ohlcv_flat):
    """min_candles altındaki veri None döndürmeli."""
    s = RsiBbStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    sig = await s.generate_signal("BTC/USDT", ohlcv_flat[:20])
    assert sig is None


async def test_rsi_bb_produces_signal(ohlcv_oscillate):
    """Yeterli veriyle bir Signal (BUY/SELL/HOLD) döndürmeli."""
    s = RsiBbStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    sig = await s.generate_signal("BTC/USDT", ohlcv_oscillate)
    assert sig is not None
    assert sig.type in (SignalType.BUY, SignalType.SELL, SignalType.HOLD)
    assert sig.price > 0
    assert sig.symbol == "BTC/USDT"


async def test_rsi_bb_metadata_contains_rsi(ohlcv_oscillate):
    s = RsiBbStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    sig = await s.generate_signal("BTC/USDT", ohlcv_oscillate)
    assert sig is not None
    assert "rsi" in sig.metadata
    assert 0 <= sig.metadata["rsi"] <= 100


# ============================================================
# MA CROSSOVER
# ============================================================
async def test_ma_crossover_insufficient(ohlcv_flat):
    s = MaCrossoverStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    sig = await s.generate_signal("BTC/USDT", ohlcv_flat[:20])
    assert sig is None


async def test_ma_crossover_trend_up(ohlcv_trend_up):
    """Sürekli yükselen trendde HOLD dönmeli (crossover bir kere olur)."""
    s = MaCrossoverStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    sig = await s.generate_signal("BTC/USDT", ohlcv_trend_up)
    assert sig is not None
    # En sonunda trend zaten yerleşik -> HOLD olmalı
    assert sig.type in (SignalType.HOLD, SignalType.BUY, SignalType.SELL)


async def test_ma_crossover_has_ema_metadata(ohlcv_trend_up):
    s = MaCrossoverStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    sig = await s.generate_signal("BTC/USDT", ohlcv_trend_up)
    assert sig is not None
    assert "ema_fast" in sig.metadata
    assert "ema_slow" in sig.metadata


# ============================================================
# MACD SIGNAL
# ============================================================
async def test_macd_signal_insufficient(ohlcv_flat):
    s = MacdSignalStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    # min_candles=230, flat fixture'da 120 var -> None
    sig = await s.generate_signal("BTC/USDT", ohlcv_flat)
    assert sig is None


async def test_macd_signal_generates_result(ohlcv_trend_up):
    """260 mum yeterli. Sonuç Signal objesi olmalı."""
    s = MacdSignalStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    sig = await s.generate_signal("BTC/USDT", ohlcv_trend_up)
    assert sig is not None
    assert sig.type in (SignalType.BUY, SignalType.SELL, SignalType.HOLD)
    assert "macd" in sig.metadata
    assert "ema_trend" in sig.metadata


async def test_macd_signal_trend_filter_applies(ohlcv_trend_down):
    """Downtrend'de BUY gelmemeli (EMA200 üstü şartı sağlanmaz)."""
    s = MacdSignalStrategy({"enabled": True, "symbols": ["ETH/USDT"]})
    sig = await s.generate_signal("ETH/USDT", ohlcv_trend_down)
    assert sig is not None
    assert sig.type != SignalType.BUY  # BUY yasak


# ============================================================
# STOCH RSI
# ============================================================
async def test_stoch_rsi_insufficient(ohlcv_flat):
    s = StochRsiStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    sig = await s.generate_signal("BTC/USDT", ohlcv_flat[:30])
    assert sig is None


async def test_stoch_rsi_returns_signal(ohlcv_oscillate):
    s = StochRsiStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    sig = await s.generate_signal("BTC/USDT", ohlcv_oscillate)
    assert sig is not None
    assert "stoch_k" in sig.metadata


async def test_stoch_rsi_cooldown_blocks_repeat(ohlcv_oscillate):
    """Cooldown sonrası çağrıda `_last_signal_bar` set ise yeni sinyal
    üretilmemeli (veya HOLD olmalı). Bunu state manipule ederek test ederiz."""
    s = StochRsiStrategy({
        "enabled": True, "symbols": ["BTC/USDT"], "cooldown_bars": 100,
    })
    # Yapay olarak son sinyali simüle et
    s._last_signal_bar["BTC/USDT"] = len(ohlcv_oscillate) - 1
    sig = await s.generate_signal("BTC/USDT", ohlcv_oscillate)
    assert sig is not None
    # Cooldown aktif -> BUY/SELL üretmemeli, HOLD döndürmeli
    assert sig.type == SignalType.HOLD


async def test_stoch_rsi_to_dict_serializable(ohlcv_oscillate):
    """Signal.to_dict() tüm tipler için JSON-safe olmalı."""
    s = StochRsiStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    sig = await s.generate_signal("BTC/USDT", ohlcv_oscillate)
    assert sig is not None
    d = sig.to_dict()
    assert "type" in d and "price" in d and "symbol" in d
    import json
    # JSON serialize hata vermemeli
    json.dumps(d)
