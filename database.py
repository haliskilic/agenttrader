"""
AgentTrader - Veritabanı Modülü
SQLite + SQLAlchemy ile trade geçmişi, bot state ve price history yönetimi.
"""

import logging
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Optional, List, Dict, Any


def _utcnow() -> datetime:
    """Python 3.12+ uyumlu UTC timestamp (_utcnow() deprecate edildi).
    Naive döndürür - SQLAlchemy DateTime kolonu ile uyumluluk için."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from config import Config

logger = logging.getLogger(__name__)

Base = declarative_base()


class Trade(Base):
    """Trade kaydı"""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(100), nullable=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)  # buy / sell
    amount = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    cost = Column(Float, nullable=False)  # amount * price
    fee = Column(Float, default=0.0)
    pnl = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    strategy = Column(String(50), nullable=False, index=True)
    status = Column(String(20), default="filled")  # pending, filled, cancelled, failed
    order_type = Column(String(20), default="market")  # market, limit
    note = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=_utcnow, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "amount": self.amount,
            "price": self.price,
            "cost": self.cost,
            "fee": self.fee,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "strategy": self.strategy,
            "status": self.status,
            "order_type": self.order_type,
            "note": self.note,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class BotState(Base):
    """Bot state anahtar-değer deposu"""
    __tablename__ = "bot_state"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class PriceHistory(Base):
    """OHLCV fiyat geçmişi cache"""
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", name="uix_price"),
        Index("idx_symbol_tf_ts", "symbol", "timeframe", "timestamp"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": int(self.timestamp.timestamp()),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class SignalRecord(Base):
    """Strateji sinyal kaydı — trade olmasa bile kaydedilir"""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    strategy = Column(String(50), nullable=False)
    signal_type = Column(String(10), nullable=False)  # buy / sell
    price = Column(Float, nullable=False)
    strength = Column(Float, default=1.0)
    reason = Column(Text, nullable=True)
    executed = Column(Integer, default=0)  # 1=trade verildi, 0=sadece sinyal
    timestamp = Column(DateTime, default=_utcnow, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "signal_type": self.signal_type,
            "price": self.price,
            "strength": self.strength,
            "reason": self.reason,
            "executed": bool(self.executed),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class LogEntry(Base):
    """Uygulama log kaydı"""
    __tablename__ = "log_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    level = Column(String(20), nullable=False)
    source = Column(String(50), nullable=True)
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=_utcnow, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "source": self.source,
            "message": self.message,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class Database:
    """Veritabanı işlemleri için yönetici sınıf"""

    def __init__(self, db_url: str = None):
        self.db_url = db_url or Config.DB_URL
        Config.ensure_dirs()
        self.engine = create_engine(
            self.db_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.init_db()

    def init_db(self):
        """Tabloları oluştur"""
        try:
            Base.metadata.create_all(bind=self.engine)
            logger.info("Veritabani tablolari hazir")
        except Exception as e:
            logger.error(f"DB init hatasi: {e}")
            raise

    @contextmanager
    def session_scope(self) -> Session:
        """Thread-safe session context manager"""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"DB transaction hatasi: {e}")
            raise
        finally:
            session.close()

    # ============================================================
    # TRADE İŞLEMLERİ
    # ============================================================
    def add_trade(self, trade_data: Dict[str, Any]) -> int:
        """Yeni trade ekle ve ID döndür"""
        with self.session_scope() as session:
            trade = Trade(**trade_data)
            session.add(trade)
            session.flush()
            return trade.id

    def get_trades(
        self, symbol: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Trade listesini al"""
        with self.session_scope() as session:
            q = session.query(Trade)
            if symbol:
                q = q.filter(Trade.symbol == symbol)
            q = q.order_by(Trade.timestamp.desc()).limit(limit)
            return [t.to_dict() for t in q.all()]

    def get_trades_since(
        self, cutoff: datetime, limit: int = 500,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Cutoff tarihinden sonraki filled trade'leri döndür.

        compute_metrics gibi çağıranların tüm trade'leri RAM'e çekmek yerine
        DB seviyesinde filtrelemesi için.
        """
        with self.session_scope() as session:
            q = session.query(Trade).filter(
                Trade.timestamp >= cutoff,
                Trade.status == "filled",
            )
            if symbol:
                q = q.filter(Trade.symbol == symbol)
            q = q.order_by(Trade.timestamp.desc()).limit(limit)
            return [t.to_dict() for t in q.all()]

    def get_trade_stats(self, days: int = 30) -> Dict[str, Any]:
        """Trade istatistikleri"""
        cutoff = _utcnow() - timedelta(days=days)
        with self.session_scope() as session:
            trades = (
                session.query(Trade)
                .filter(Trade.timestamp >= cutoff, Trade.status == "filled")
                .all()
            )
            if not trades:
                return {
                    "total_trades": 0,
                    "total_pnl": 0.0,
                    "win_rate": 0.0,
                    "winners": 0,
                    "losers": 0,
                    "avg_pnl": 0.0,
                    "best_trade": 0.0,
                    "worst_trade": 0.0,
                }

            total_pnl = sum(t.pnl or 0 for t in trades)
            winners = [t for t in trades if (t.pnl or 0) > 0]
            losers = [t for t in trades if (t.pnl or 0) < 0]
            win_rate = (len(winners) / len(trades) * 100) if trades else 0.0

            return {
                "total_trades": len(trades),
                "total_pnl": round(total_pnl, 4),
                "win_rate": round(win_rate, 2),
                "winners": len(winners),
                "losers": len(losers),
                "avg_pnl": round(total_pnl / len(trades), 4) if trades else 0.0,
                "best_trade": round(max((t.pnl or 0) for t in trades), 4),
                "worst_trade": round(min((t.pnl or 0) for t in trades), 4),
            }

    def get_daily_pnl(self) -> float:
        """Bugünkü toplam PnL"""
        today_start = _utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        with self.session_scope() as session:
            trades = (
                session.query(Trade)
                .filter(Trade.timestamp >= today_start, Trade.status == "filled")
                .all()
            )
            return sum(t.pnl or 0 for t in trades)

    # ============================================================
    # BOT STATE
    # ============================================================
    def set_state(self, key: str, value: str):
        """State değeri kaydet"""
        with self.session_scope() as session:
            state = session.query(BotState).filter(BotState.key == key).first()
            if state:
                state.value = value
                state.updated_at = _utcnow()
            else:
                state = BotState(key=key, value=value)
                session.add(state)

    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """State değeri oku"""
        with self.session_scope() as session:
            state = session.query(BotState).filter(BotState.key == key).first()
            return state.value if state else default

    # ============================================================
    # PRICE HISTORY
    # ============================================================
    def save_ohlcv(self, symbol: str, timeframe: str, candles: List[List]):
        """OHLCV verilerini cache'le (idempotent).

        Batch upsert ile N+1 sorgu yerine tek INSERT ... ON CONFLICT DO UPDATE
        çağrısı kullanır. SQLite dialect'ine özgü ama test/prod her ikisi de
        SQLite kullanıyor; başka DB'ye geçerken adapt edilmeli.
        """
        if not candles:
            return
        try:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            rows = [
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc).replace(tzinfo=None),
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5],
                }
                for c in candles
            ]
            with self.session_scope() as session:
                stmt = sqlite_insert(PriceHistory).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["symbol", "timeframe", "timestamp"],
                    set_={
                        "open": stmt.excluded.open,
                        "high": stmt.excluded.high,
                        "low": stmt.excluded.low,
                        "close": stmt.excluded.close,
                        "volume": stmt.excluded.volume,
                    },
                )
                session.execute(stmt)
        except Exception as e:
            # Fallback: eski yöntem (yavaş ama her DB'de çalışır)
            logger.debug(f"save_ohlcv batch fallback: {e}")
            with self.session_scope() as session:
                for c in candles:
                    ts = datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc).replace(tzinfo=None)
                    existing = (
                        session.query(PriceHistory)
                        .filter(
                            PriceHistory.symbol == symbol,
                            PriceHistory.timeframe == timeframe,
                            PriceHistory.timestamp == ts,
                        )
                        .first()
                    )
                    if existing:
                        existing.open = c[1]
                        existing.high = c[2]
                        existing.low = c[3]
                        existing.close = c[4]
                        existing.volume = c[5]
                    else:
                        session.add(
                            PriceHistory(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=ts,
                                open=c[1],
                                high=c[2],
                                low=c[3],
                                close=c[4],
                                volume=c[5],
                            )
                        )

    def get_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Cache'den OHLCV oku"""
        with self.session_scope() as session:
            rows = (
                session.query(PriceHistory)
                .filter(
                    PriceHistory.symbol == symbol,
                    PriceHistory.timeframe == timeframe,
                )
                .order_by(PriceHistory.timestamp.desc())
                .limit(limit)
                .all()
            )
            rows = list(reversed(rows))
            return [r.to_dict() for r in rows]

    # ============================================================
    # SİNYALLER
    # ============================================================
    def save_signal(self, symbol: str, strategy: str, signal_type: str,
                    price: float, strength: float = 1.0, reason: str = "",
                    executed: bool = False) -> int:
        with self.session_scope() as session:
            rec = SignalRecord(
                symbol=symbol, strategy=strategy, signal_type=signal_type,
                price=price, strength=strength, reason=reason,
                executed=1 if executed else 0,
            )
            session.add(rec)
            session.flush()
            return rec.id

    def mark_signal_executed(self, signal_id: int):
        with self.session_scope() as session:
            rec = session.query(SignalRecord).filter(SignalRecord.id == signal_id).first()
            if rec:
                rec.executed = 1

    def get_signals(self, symbol: Optional[str] = None, limit: int = 100,
                    strategy: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.session_scope() as session:
            q = session.query(SignalRecord)
            if symbol:
                q = q.filter(SignalRecord.symbol == symbol)
            if strategy:
                q = q.filter(SignalRecord.strategy == strategy)
            q = q.order_by(SignalRecord.timestamp.desc()).limit(limit)
            return [r.to_dict() for r in q.all()]

    # ============================================================
    # LOGS
    # ============================================================
    def add_log(self, level: str, source: str, message: str):
        """Log kaydı ekle"""
        try:
            with self.session_scope() as session:
                entry = LogEntry(level=level, source=source, message=message)
                session.add(entry)
        except Exception as e:
            logger.error(f"Log eklenemedi: {e}")

    def get_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Son logları al"""
        with self.session_scope() as session:
            rows = (
                session.query(LogEntry)
                .order_by(LogEntry.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in rows]


# Global instance
db = Database()
