"""
Ortak pytest fixture'ları.

Tüm testler production SQLite DB'sini KİRLETMEMELİ: `temp_db` fixture'ı
izole bir SQLite dosyası hazırlar ve testten sonra siler. `Database`
örneği `database.db` global singleton'unu geçici olarak değiştirir.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

# Proje kökünü PYTHONPATH'e ekle (tests/ alt dizininden çalıştırıldığında)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ============================================================
# OHLCV YARDIMCILARI
# ============================================================
def make_candle(ts_ms: int, o: float, h: float, l: float, c: float, v: float = 100.0):
    """Tek bir OHLCV mumu."""
    return [ts_ms, o, h, l, c, v]


def synthetic_ohlcv(
    n: int = 250,
    start_ts_ms: int = 1_700_000_000_000,
    start_price: float = 100.0,
    pattern: str = "trend",
) -> List[List]:
    """Sentetik OHLCV listesi üret.

    pattern:
        "flat"       -> yatay
        "trend"      -> yukarı trend
        "downtrend"  -> aşağı trend
        "oscillate"  -> sinüs-benzeri osilasyon (RSI/StochRSI testleri için)
        "spike_vol"  -> son barda yüksek hacim (volume filter testleri için)
    """
    import math
    candles: List[List] = []
    price = start_price
    step_ms = 60 * 60 * 1000  # 1h mum
    for i in range(n):
        if pattern == "flat":
            delta = 0.0
        elif pattern == "trend":
            delta = 0.4
        elif pattern == "downtrend":
            delta = -0.4
        elif pattern == "oscillate":
            delta = math.sin(i / 5.0) * 1.5
        elif pattern == "spike_vol":
            delta = 0.1
        else:
            delta = 0.0
        o = price
        c = price + delta
        h = max(o, c) + 0.2
        l = min(o, c) - 0.2
        v = 100.0
        if pattern == "spike_vol" and i == n - 1:
            v = 1000.0
        candles.append(make_candle(start_ts_ms + i * step_ms, o, h, l, c, v))
        price = c
    return candles


@pytest.fixture
def ohlcv_trend_up() -> List[List]:
    return synthetic_ohlcv(n=260, pattern="trend", start_price=100.0)


@pytest.fixture
def ohlcv_trend_down() -> List[List]:
    return synthetic_ohlcv(n=260, pattern="downtrend", start_price=1000.0)


@pytest.fixture
def ohlcv_oscillate() -> List[List]:
    return synthetic_ohlcv(n=120, pattern="oscillate", start_price=100.0)


@pytest.fixture
def ohlcv_flat() -> List[List]:
    return synthetic_ohlcv(n=120, pattern="flat", start_price=100.0)


# ============================================================
# DB FIXTURE
# ============================================================
@pytest.fixture
def temp_db(monkeypatch):
    """İzole SQLite DB fixture.

    Her test fonksiyonu için ayrı bir DB dosyası oluşturur, `database.db`
    singleton'unu monkeypatch ile override eder, test sonunda siler.
    """
    from database import Database
    import database as _dbmod

    tmp = tempfile.NamedTemporaryFile(
        suffix=".db", delete=False, prefix="agenttrader_test_"
    )
    tmp.close()
    try:
        url = f"sqlite:///{Path(tmp.name).as_posix()}"
        test_db = Database(db_url=url)
        monkeypatch.setattr(_dbmod, "db", test_db)
        yield test_db
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ============================================================
# MOCK EXCHANGE
# ============================================================
class MockExchange:
    """Backtester testleri için minimal async mock exchange.

    `get_ohlcv` ve `get_balance` gibi async metodları taklit eder. İçindeki
    data `set_ohlcv()` ile test tarafından kontrol edilir.
    """

    def __init__(self):
        self._ohlcv: Dict[tuple, List[List]] = {}
        self._client = object()  # connect()'siz bile "bağlı" görün

    def set_ohlcv(self, symbol: str, timeframe: str, candles: List[List]):
        self._ohlcv[(symbol, timeframe)] = candles

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 200):
        return list(self._ohlcv.get((symbol, timeframe), []))[-limit:]

    async def connect(self):
        return

    async def close(self):
        return

    async def get_balance(self):
        return {"total": {"USDT": 10000}, "free": {"USDT": 10000}, "used": {},
                "usdt_value": 10000}


@pytest.fixture
def mock_exchange():
    return MockExchange()
