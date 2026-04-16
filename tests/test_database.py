"""
Database CRUD testleri.

Her test `temp_db` fixture'ı ile izole SQLite DB üzerinde çalışır.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_add_and_get_trade(temp_db):
    tid = temp_db.add_trade({
        "order_id": "abc-1",
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.01,
        "price": 50000,
        "cost": 500,
        "fee": 0.5,
        "pnl": 0.0,
        "strategy": "rsi_bb",
        "status": "filled",
        "order_type": "market",
        "note": "test",
    })
    assert tid > 0
    trades = temp_db.get_trades(limit=10)
    assert len(trades) == 1
    assert trades[0]["order_id"] == "abc-1"
    assert trades[0]["strategy"] == "rsi_bb"


def test_filter_trades_by_symbol(temp_db):
    for sym in ["BTC/USDT", "ETH/USDT", "BTC/USDT"]:
        temp_db.add_trade({
            "order_id": f"o-{sym}", "symbol": sym, "side": "buy",
            "amount": 1, "price": 1, "cost": 1, "strategy": "x",
            "status": "filled",
        })
    btc = temp_db.get_trades(symbol="BTC/USDT", limit=10)
    eth = temp_db.get_trades(symbol="ETH/USDT", limit=10)
    assert len(btc) == 2
    assert len(eth) == 1


def test_trade_stats_aggregates(temp_db):
    # 3 winning, 2 losing
    for pnl in [10.0, 20.0, 30.0, -5.0, -15.0]:
        temp_db.add_trade({
            "order_id": f"o-{pnl}", "symbol": "BTC/USDT", "side": "sell",
            "amount": 1, "price": 100, "cost": 100, "pnl": pnl,
            "strategy": "rsi_bb", "status": "filled",
        })
    stats = temp_db.get_trade_stats(days=1)
    assert stats["total_trades"] == 5
    assert stats["winners"] == 3
    assert stats["losers"] == 2
    assert stats["total_pnl"] == pytest.approx(40.0)
    assert stats["best_trade"] == pytest.approx(30.0)
    assert stats["worst_trade"] == pytest.approx(-15.0)
    assert stats["win_rate"] == pytest.approx(60.0)


def test_state_set_and_get(temp_db):
    temp_db.set_state("bot_status", "running")
    assert temp_db.get_state("bot_status") == "running"
    temp_db.set_state("bot_status", "stopped")
    assert temp_db.get_state("bot_status") == "stopped"
    assert temp_db.get_state("nonexistent") is None
    assert temp_db.get_state("nonexistent", default="x") == "x"


def test_signals_crud(temp_db):
    sid = temp_db.save_signal(
        symbol="BTC/USDT", strategy="macd_signal",
        signal_type="buy", price=50000, strength=0.9,
        reason="test", executed=False,
    )
    assert sid > 0
    sigs = temp_db.get_signals(limit=5)
    assert len(sigs) == 1
    assert sigs[0]["executed"] is False
    temp_db.mark_signal_executed(sid)
    sigs2 = temp_db.get_signals(limit=5)
    assert sigs2[0]["executed"] is True


def test_ohlcv_save_and_read(temp_db):
    candles = [
        [1700000000000, 100, 110, 95, 105, 1000],
        [1700003600000, 105, 115, 100, 112, 1500],
    ]
    temp_db.save_ohlcv("BTC/USDT", "1h", candles)
    rows = temp_db.get_ohlcv("BTC/USDT", "1h", limit=10)
    assert len(rows) == 2
    # get_ohlcv chronological sıralı (reversed desc) döndürüyor
    assert rows[0]["open"] == 100
    assert rows[1]["close"] == 112


def test_logs_crud(temp_db):
    temp_db.add_log("INFO", "test", "merhaba")
    temp_db.add_log("ERROR", "test", "hata")
    logs = temp_db.get_logs(limit=10)
    assert len(logs) == 2
    # Son eklenen önce gelir
    assert logs[0]["level"] == "ERROR"
