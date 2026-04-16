"""
Portföy Takip Modülü
- Bakiye, açık pozisyonlar
- Gerçekleşmiş / Gerçekleşmemiş PnL
- Performans metrikleri (Sharpe, Sortino, Win rate, vb.)
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from database import db


def _utcnow() -> datetime:
    """UTC timestamp - _utcnow() deprecated in Python 3.12+."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Açık pozisyon"""
    symbol: str
    side: str            # long / short
    amount: float
    entry_price: float
    current_price: float = 0.0
    strategy: str = ""
    opened_at: datetime = field(default_factory=_utcnow)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    @property
    def unrealized_pnl(self) -> float:
        if self.current_price <= 0:
            return 0.0
        if self.side == "long":
            return (self.current_price - self.entry_price) * self.amount
        return (self.entry_price - self.current_price) * self.amount

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.side == "long":
            return (self.current_price - self.entry_price) / self.entry_price * 100
        return (self.entry_price - self.current_price) / self.entry_price * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "amount": self.amount,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "unrealized_pnl": round(self.unrealized_pnl, 4),
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct, 4),
            "strategy": self.strategy,
            "opened_at": self.opened_at.isoformat(),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
        }


class Portfolio:
    """Portföy durumu yöneticisi"""

    def __init__(self):
        self.positions: Dict[str, Position] = {}  # key: symbol
        self.balance: Dict[str, Any] = {"total": {}, "free": {}, "used": {}, "usdt_value": 0.0}
        self.last_update: Optional[datetime] = None

    # ============================================================
    # POZİSYON YÖNETİMİ
    # ============================================================
    def add_position(self, position: Position):
        """Açık pozisyon ekle"""
        self.positions[position.symbol] = position
        logger.info(
            f"Pozisyon acildi: {position.side} {position.amount} "
            f"{position.symbol} @ {position.entry_price}"
        )

    def close_position(
        self, symbol: str, exit_price: float
    ) -> Optional[Dict[str, Any]]:
        """Pozisyonu kapat ve gerçekleşmiş PnL döndür"""
        pos = self.positions.pop(symbol, None)
        if not pos:
            return None
        if exit_price <= 0 or pos.entry_price <= 0:
            logger.error(
                f"close_position: gecersiz fiyat: entry={pos.entry_price}, exit={exit_price}"
            )
            # Pozisyonu geri koy, kapatma
            self.positions[symbol] = pos
            return None
        if pos.side == "long":
            pnl = (exit_price - pos.entry_price) * pos.amount
        else:
            pnl = (pos.entry_price - exit_price) * pos.amount

        pnl_pct = (
            ((exit_price - pos.entry_price) / pos.entry_price * 100)
            if pos.side == "long"
            else ((pos.entry_price - exit_price) / pos.entry_price * 100)
        )
        logger.info(
            f"Pozisyon kapandi: {symbol} PnL: {pnl:.4f} ({pnl_pct:.2f}%)"
        )
        return {
            "symbol": symbol,
            "side": pos.side,
            "amount": pos.amount,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "pnl": round(pnl, 6),
            "pnl_pct": round(pnl_pct, 4),
            "strategy": pos.strategy,
            "opened_at": pos.opened_at,
        }

    def update_prices(self, prices: Dict[str, float]):
        """Açık pozisyonlar için anlık fiyatları güncelle"""
        for symbol, price in prices.items():
            if symbol in self.positions:
                self.positions[symbol].current_price = price

    def get_position(self, symbol: str) -> Optional[Position]:
        return self.positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def open_positions_count(self) -> int:
        return len(self.positions)

    def open_positions_list(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self.positions.values()]

    # ============================================================
    # BAKİYE
    # ============================================================
    def update_balance(self, balance: Dict[str, Any]):
        """Bakiye güncelle"""
        self.balance = balance
        self.last_update = _utcnow()

    @property
    def total_usdt(self) -> float:
        return self.balance.get("usdt_value", 0.0)

    @property
    def free_usdt(self) -> float:
        return self.balance.get("free", {}).get("USDT", 0.0)

    # ============================================================
    # PNL
    # ============================================================
    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values())

    def total_realized_pnl(self, days: int = 30) -> float:
        stats = db.get_trade_stats(days=days)
        return stats.get("total_pnl", 0.0)

    # ============================================================
    # PERFORMANS METRİKLERİ
    # ============================================================
    def compute_metrics(self, days: int = 30) -> Dict[str, Any]:
        """Sharpe, Sortino, Win rate vb. metrikleri hesapla.

        DB seviyesinde cutoff + status filtresi uygulanarak tüm trade
        geçmişini RAM'e yüklemek yerine yalnızca ilgili pencereyi çekiyoruz.
        """
        cutoff = _utcnow() - timedelta(days=days)
        recent = db.get_trades_since(cutoff, limit=500)

        if not recent:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "max_drawdown": 0.0,
                "total_pnl": 0.0,
            }

        pnls = [t.get("pnl") or 0 for t in recent]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p < 0]
        total = sum(pnls)

        win_rate = (len(winners) / len(pnls) * 100) if pnls else 0.0
        avg_win = sum(winners) / len(winners) if winners else 0.0
        avg_loss = sum(losers) / len(losers) if losers else 0.0
        profit_factor = (sum(winners) / abs(sum(losers))) if losers else float("inf")

        # Sharpe ratio (risk-free = 0)
        mean_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls) if pnls else 0.0
        std_dev = math.sqrt(variance)
        sharpe = (mean_pnl / std_dev * math.sqrt(252)) if std_dev > 0 else 0.0

        # Sortino ratio (downside deviation)
        # Downside deviation = sqrt( sum(min(0, r - target)^2) / n )
        # target = 0 olduğu için downside = negatif returnlerin karesi ortalaması
        downside = [p for p in pnls if p < 0]
        if downside:
            down_var = sum(p ** 2 for p in downside) / len(downside)
            down_std = math.sqrt(down_var)
            sortino = (mean_pnl / down_std * math.sqrt(252)) if down_std > 0 else 0.0
        else:
            # Zararlı trade yok
            sortino = float("inf") if mean_pnl > 0 else 0.0

        # Max drawdown (running)
        cum = []
        running = 0.0
        for p in pnls:
            running += p
            cum.append(running)
        peak = 0.0
        max_dd = 0.0
        for v in cum:
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd

        # JSON serializability için inf değerleri None'a çevir
        def _safe(v):
            return None if (v == float("inf") or v == float("-inf") or math.isnan(v)) else round(v, 3)

        return {
            "total_trades": len(recent),
            "win_rate": round(win_rate, 2),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": _safe(profit_factor),
            "sharpe_ratio": _safe(sharpe),
            "sortino_ratio": _safe(sortino),
            "max_drawdown": round(max_dd, 4),
            "total_pnl": round(total, 4),
        }

    def get_summary(self) -> Dict[str, Any]:
        """Tam portföy özeti"""
        return {
            "balance": self.balance,
            "total_usdt": self.total_usdt,
            "free_usdt": self.free_usdt,
            "open_positions": self.open_positions_list(),
            "open_positions_count": self.open_positions_count(),
            "unrealized_pnl": round(self.total_unrealized_pnl, 4),
            "daily_pnl": round(db.get_daily_pnl(), 4),
            "last_update": self.last_update.isoformat() if self.last_update else None,
        }
