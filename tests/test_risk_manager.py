"""
RiskManager birim testleri.

Burada DB kullanıyoruz (update_balance_snapshot günlük bakiyeyi DB'ye yazar).
Her test `temp_db` fixture'ı ile izole DB'de çalışır.
"""

from __future__ import annotations

import pytest

from bot.risk_manager import RiskManager


def test_position_size_normal(temp_db):
    rm = RiskManager()
    rm.max_position_pct = 0.05  # %5
    # 10000 USDT, BTC 50000 USD -> 0.01 BTC (10000 * 0.05 / 50000)
    amount = rm.calculate_position_size(balance_usdt=10000, price=50000)
    assert amount == pytest.approx(0.01, rel=1e-6)


def test_position_size_zero_price(temp_db):
    rm = RiskManager()
    assert rm.calculate_position_size(10000, 0) == 0.0
    assert rm.calculate_position_size(0, 50000) == 0.0


def test_stop_loss_take_profit_buy(temp_db):
    rm = RiskManager()
    rm.stop_loss_pct = 0.02
    rm.take_profit_pct = 0.04
    sl = rm.calculate_stop_loss(100, "buy")
    tp = rm.calculate_take_profit(100, "buy")
    assert sl == pytest.approx(98.0)
    assert tp == pytest.approx(104.0)


def test_stop_loss_take_profit_sell(temp_db):
    rm = RiskManager()
    rm.stop_loss_pct = 0.02
    rm.take_profit_pct = 0.04
    sl = rm.calculate_stop_loss(100, "sell")
    tp = rm.calculate_take_profit(100, "sell")
    # Sell için yönler tersine
    assert sl == pytest.approx(102.0)
    assert tp == pytest.approx(96.0)


def test_validate_order_approves_normal(temp_db):
    rm = RiskManager()
    rm.update_balance_snapshot(10000)
    rc = rm.validate_order(
        symbol="BTC/USDT", side="buy", proposed_amount=0.01, price=50000,
        balance_usdt=10000, open_positions=0, current_balance=10000,
    )
    assert rc.approved is True
    assert rc.adjusted_amount is not None


def test_validate_order_rejects_over_max_pct(temp_db):
    """Miktar max_position_pct'yi aşarsa adjusted_amount ile azaltılır."""
    rm = RiskManager()
    rm.max_position_pct = 0.05  # 10000 * 5% = 500 USD max
    rm.update_balance_snapshot(10000)
    # 1 BTC * 50000 = 50000 USD -> kesinlikle limit üstü
    rc = rm.validate_order(
        symbol="BTC/USDT", side="buy", proposed_amount=1.0, price=50000,
        balance_usdt=10000, open_positions=0, current_balance=10000,
    )
    # Adjust edilmeli ya da red
    if rc.approved:
        assert rc.adjusted_amount is not None
        assert rc.adjusted_amount * 50000 <= 10000 * 0.05 + 0.01


def test_validate_order_rejects_open_positions_limit(temp_db):
    rm = RiskManager()
    rm.max_open_positions = 2
    rm.update_balance_snapshot(10000)
    rc = rm.validate_order(
        symbol="BTC/USDT", side="buy", proposed_amount=0.001, price=50000,
        balance_usdt=10000, open_positions=2, current_balance=10000,
    )
    assert rc.approved is False
    assert "pozisyon" in rc.reason.lower()


def test_trailing_stop_long_moves_up(temp_db):
    """Trailing SL fiyat yukarı gittikçe yukarı çekilir, aşağı gelince SABİT kalır."""
    rm = RiskManager()
    sl1 = rm.update_trailing_stop("BTC/USDT", 100.0, "long", trail_pct=0.02)
    assert sl1 == pytest.approx(98.0)

    # Fiyat 110'a çıktı -> trailing 107.8 olmalı
    sl2 = rm.update_trailing_stop("BTC/USDT", 110.0, "long", trail_pct=0.02)
    assert sl2 == pytest.approx(107.8)

    # Fiyat 105'e düştü -> trailing SABIT kalır (geri gelmemeli)
    sl3 = rm.update_trailing_stop("BTC/USDT", 105.0, "long", trail_pct=0.02)
    assert sl3 == pytest.approx(107.8)


def test_trailing_stop_clear(temp_db):
    rm = RiskManager()
    rm.update_trailing_stop("BTC/USDT", 100.0, "long")
    assert rm.get_trailing_stop("BTC/USDT") is not None
    rm.clear_trailing_stop("BTC/USDT")
    assert rm.get_trailing_stop("BTC/USDT") is None


def test_trailing_stop_short(temp_db):
    """Short için trailing aşağı iner, yukarı çıkmaz."""
    rm = RiskManager()
    sl1 = rm.update_trailing_stop("ETH/USDT", 1000.0, "short", trail_pct=0.02)
    assert sl1 == pytest.approx(1020.0)

    # Fiyat 900'e düştü -> trail 918 olmalı (daha sıkı)
    sl2 = rm.update_trailing_stop("ETH/USDT", 900.0, "short", trail_pct=0.02)
    assert sl2 == pytest.approx(918.0)

    # Fiyat geri 950'ye çıktı -> trail SABIT (daha gevşek olmaz)
    sl3 = rm.update_trailing_stop("ETH/USDT", 950.0, "short", trail_pct=0.02)
    assert sl3 == pytest.approx(918.0)
