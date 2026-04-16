"""
Backtester testleri.

MockExchange ile strateji backtest'i çalıştırıp sonuç yapısını doğrular.
"""

from __future__ import annotations

import pytest

from bot.backtester import Backtester
from bot.strategies import RsiBbStrategy, MaCrossoverStrategy, MacdSignalStrategy


pytestmark = pytest.mark.asyncio


async def test_backtester_runs_rsi_bb(mock_exchange, ohlcv_oscillate, temp_db):
    mock_exchange.set_ohlcv("BTC/USDT", "15m", ohlcv_oscillate)
    strat = RsiBbStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    bt = Backtester(mock_exchange, {"rsi_bb": strat})
    res = await bt.run(
        symbol="BTC/USDT", timeframe="15m", strategy_name="rsi_bb",
        initial_balance=1000.0, limit=120,
    )
    assert res.symbol == "BTC/USDT"
    assert res.strategy == "rsi_bb"
    assert res.initial_balance == 1000.0
    assert res.total_trades >= 0
    assert isinstance(res.to_dict(), dict)


async def test_backtester_insufficient_data_raises(mock_exchange, temp_db):
    mock_exchange.set_ohlcv("BTC/USDT", "15m", [[0, 1, 1, 1, 1, 1]] * 5)
    strat = RsiBbStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    bt = Backtester(mock_exchange, {"rsi_bb": strat})
    with pytest.raises(ValueError):
        await bt.run(
            symbol="BTC/USDT", timeframe="15m", strategy_name="rsi_bb",
            initial_balance=1000.0, limit=5,
        )


async def test_backtester_unknown_strategy_raises(mock_exchange, temp_db):
    bt = Backtester(mock_exchange, {})
    with pytest.raises(ValueError):
        await bt.run(
            symbol="BTC/USDT", timeframe="15m", strategy_name="nonexistent",
            initial_balance=1000.0,
        )


async def test_backtester_equity_curve_returned(mock_exchange, ohlcv_trend_up, temp_db):
    mock_exchange.set_ohlcv("BTC/USDT", "1h", ohlcv_trend_up)
    strat = MaCrossoverStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    bt = Backtester(mock_exchange, {"ma_crossover": strat})
    res = await bt.run(
        symbol="BTC/USDT", timeframe="1h", strategy_name="ma_crossover",
        initial_balance=1000.0, limit=260,
    )
    assert isinstance(res.equity_curve, list)
    assert len(res.equity_curve) > 0
    # Her nokta time+value içermeli
    for pt in res.equity_curve[:3]:
        assert "time" in pt and "value" in pt


async def test_backtester_signals_list(mock_exchange, ohlcv_trend_up, temp_db):
    mock_exchange.set_ohlcv("BTC/USDT", "1h", ohlcv_trend_up)
    strat = MacdSignalStrategy({"enabled": True, "symbols": ["BTC/USDT"]})
    bt = Backtester(mock_exchange, {"macd_signal": strat})
    res = await bt.run(
        symbol="BTC/USDT", timeframe="1h", strategy_name="macd_signal",
        initial_balance=1000.0, limit=260,
    )
    # signals listesi (boş olabilir ama liste tipinde olmalı)
    assert isinstance(res.signals, list)
    # to_dict JSON-safe
    import json
    json.dumps(res.to_dict(), default=str)
