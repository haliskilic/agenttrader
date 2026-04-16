"""
Paper Trading Engine

Gerçek emirler vermeden canlı fiyatlarla simüle eder. Trader ile aynı
arayüze sahip ancak `exchange.place_order` çağrısı yerine sanal pozisyon
yönetimi yapar. Tüm paper trade'ler DB'de `strategy` alanında asıl strateji
adı + `note="PAPER: ..."` prefiksi ile kaydedilir ki canlı trade'lerden
ayırt edilebilsin.

Tasarım notları
---------------
- PaperTrader, Trader'ın yerine geçmez; paralel olarak da çalıştırılabilir.
  Singleton `get_paper_trader()` kendi instance'ını tutar.
- Slippage modellemesi: market emir executed price = live price *
  (1 +/- slippage_bps/10000). Default 5 bps — OKX spot için makul.
- Komisyon: alış/satışta `fee_rate` uygulanır (default %0.1 OKX taker).
- SL/TP takibi: ayrı bir task, live_prices üzerinden kontrol eder. Bot
  durdurulduğunda task da durur.
- Equity snapshot: 10 saniyede bir portfolyo değeri `_equity_curve`
  listesine eklenir (bounded).
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from config import Config
from database import db

from .exchange import Exchange
from .strategies import STRATEGY_REGISTRY
from .strategies.base import BaseStrategy, Signal, SignalType

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PaperTrader:
    """Paper trading motoru - canlı fiyatla sanal trade simülasyonu."""

    # Maksimum equity snapshot sayısı (bounded list)
    MAX_EQUITY_POINTS = 1000

    def __init__(
        self,
        exchange: Optional[Exchange] = None,
        initial_balance: Optional[float] = None,
        fee_rate: float = 0.001,
        slippage_bps: float = 5.0,
    ):
        self.exchange = exchange or Exchange()
        self.initial_balance = initial_balance if initial_balance is not None else Config.PAPER_BALANCE
        self.paper_balance: float = self.initial_balance
        self.paper_positions: Dict[str, Dict[str, Any]] = {}
        # Her pozisyon için: {side, amount, entry_price, entry_time,
        #                     strategy, stop_loss, take_profit, fee_paid}

        self.paper_trades: List[Dict[str, Any]] = []  # in-memory (DB'ye de yazılır)
        self._equity_curve: List[Dict[str, Any]] = []

        self.fee_rate = fee_rate
        self.slippage_bps = slippage_bps

        self.strategies: Dict[str, BaseStrategy] = {}
        self.symbols: List[str] = list(Config.DEFAULT_SYMBOLS)
        self.live_prices: Dict[str, float] = {}

        self.enabled: bool = False
        self._tasks: List[asyncio.Task] = []
        self._stop_event: Optional[asyncio.Event] = None
        self._lock: Optional[asyncio.Lock] = None
        self.notification_callbacks: List[Callable] = []

        self._initialize_strategies()

    # ============================================================
    # PRIMITIVES
    # ============================================================
    def _ensure_primitives(self):
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        if self._lock is None:
            self._lock = asyncio.Lock()

    def _initialize_strategies(self):
        """Config'deki stratejileri initialize et (Trader ile paralel)."""
        for name, cfg in Config.STRATEGY_CONFIGS.items():
            if name in STRATEGY_REGISTRY:
                # Paper mode için bağımsız instance kullan ki last_signals
                # canlı trader ile çakışmasın.
                self.strategies[name] = STRATEGY_REGISTRY[name](cfg)

    # ============================================================
    # SANAL EMİR
    # ============================================================
    def _apply_slippage(self, price: float, side: str) -> float:
        """Market emir slippage modellemesi (buy -> yukarı, sell -> aşağı)."""
        slip = price * (self.slippage_bps / 10000.0)
        return price + slip if side == "buy" else price - slip

    async def _execute_paper_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        strategy: str,
        reason: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Sanal emir gerçekleştir ve DB'ye kaydet.

        Not
        ---
        Spot simülasyon: sell emri verilirken sembolde long pozisyon olmalı.
        Short desteklenmez (OKX spot ile tutarlı).
        """
        if amount <= 0 or price <= 0:
            logger.warning(f"[PAPER] gecersiz amount/price: {amount}/{price}")
            return None

        exec_price = self._apply_slippage(price, side)
        cost = amount * exec_price
        fee = cost * self.fee_rate

        async with self._lock:
            if side == "buy":
                total_cost = cost + fee
                if total_cost > self.paper_balance:
                    logger.warning(
                        f"[PAPER] yetersiz bakiye: {self.paper_balance:.4f} < {total_cost:.4f}"
                    )
                    return None
                self.paper_balance -= total_cost

                # SL/TP — config'ten al (risk_manager yerine stratejinin kendi değerleri)
                strat = self.strategies.get(strategy)
                sl_pct = getattr(strat, "stop_loss_pct", Config.STOP_LOSS_PCT) if strat else Config.STOP_LOSS_PCT
                tp_pct = getattr(strat, "take_profit_pct", Config.TAKE_PROFIT_PCT) if strat else Config.TAKE_PROFIT_PCT

                self.paper_positions[symbol] = {
                    "side": "long",
                    "amount": amount,
                    "entry_price": exec_price,
                    "entry_time": _utcnow(),
                    "strategy": strategy,
                    "stop_loss": exec_price * (1 - sl_pct),
                    "take_profit": exec_price * (1 + tp_pct),
                    "fee_paid": fee,
                }

            elif side == "sell":
                pos = self.paper_positions.get(symbol)
                if not pos:
                    logger.debug(f"[PAPER] sell sinyali ama pozisyon yok: {symbol}")
                    return None
                # PnL hesapla (long için)
                entry = pos["entry_price"]
                pnl = (exec_price - entry) * pos["amount"] - fee - pos.get("fee_paid", 0)
                pnl_pct = ((exec_price - entry) / entry * 100) if entry > 0 else 0.0
                proceeds = pos["amount"] * exec_price - fee
                self.paper_balance += proceeds
                del self.paper_positions[symbol]

                # DB kayıt: paper close trade
                trade_record = {
                    "order_id": f"paper-{int(_utcnow().timestamp() * 1000)}",
                    "symbol": symbol,
                    "side": "sell",
                    "amount": amount,
                    "price": exec_price,
                    "cost": amount * exec_price,
                    "fee": fee,
                    "pnl": round(pnl, 6),
                    "pnl_pct": round(pnl_pct, 4),
                    "strategy": strategy,  # asıl strateji adı
                    "status": "filled",
                    "order_type": "market",
                    "note": f"PAPER: {reason} (close pos, entry={entry:.4f})",
                }
                try:
                    db.add_trade(trade_record)
                except Exception as e:
                    logger.error(f"[PAPER] DB trade kayit hatasi: {e}")
                self.paper_trades.append(trade_record)

                await self._notify({
                    "event": "paper_position_closed",
                    "trade": trade_record,
                })
                return {
                    "id": trade_record["order_id"],
                    "price": exec_price,
                    "amount": amount,
                    "pnl": pnl,
                }
            else:
                return None

        # BUY - DB kayıt
        trade_record = {
            "order_id": f"paper-{int(_utcnow().timestamp() * 1000)}",
            "symbol": symbol,
            "side": "buy",
            "amount": amount,
            "price": exec_price,
            "cost": cost,
            "fee": fee,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "strategy": strategy,
            "status": "filled",
            "order_type": "market",
            "note": f"PAPER: {reason} (open long)",
        }
        try:
            db.add_trade(trade_record)
        except Exception as e:
            logger.error(f"[PAPER] DB trade kayit hatasi: {e}")
        self.paper_trades.append(trade_record)

        await self._notify({
            "event": "paper_trade_executed",
            "trade": trade_record,
        })
        return {
            "id": trade_record["order_id"],
            "price": exec_price,
            "amount": amount,
        }

    # ============================================================
    # YAŞAM DÖNGÜSÜ
    # ============================================================
    async def start(self):
        """Paper trading motorunu başlat."""
        self._ensure_primitives()
        if self.enabled:
            logger.info("Paper trader zaten calisiyor")
            return
        self.enabled = True
        self._stop_event.clear()

        try:
            await self.exchange.connect()
        except Exception as e:
            logger.error(f"[PAPER] exchange baglanti hatasi: {e}")

        self._tasks = [
            asyncio.create_task(self._price_loop(), name="paper_price"),
            asyncio.create_task(self._strategy_loop(), name="paper_strategy"),
            asyncio.create_task(self._sl_tp_monitor(), name="paper_sltp"),
            asyncio.create_task(self._equity_snapshot_loop(), name="paper_equity"),
        ]
        logger.info(f"[PAPER] paper trader baslatildi, bakiye: {self.paper_balance:.2f}")
        await self._notify({"event": "paper_started", "balance": self.paper_balance})

    async def stop(self):
        """Paper trading motorunu durdur."""
        if not self.enabled:
            return
        self.enabled = False
        self._ensure_primitives()
        self._stop_event.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        logger.info("[PAPER] paper trader durduruldu")
        await self._notify({"event": "paper_stopped"})

    def reset(self, new_balance: Optional[float] = None):
        """Paper bakiyeyi ve geçmişi sıfırla."""
        self.initial_balance = new_balance if new_balance is not None else Config.PAPER_BALANCE
        self.paper_balance = self.initial_balance
        self.paper_positions.clear()
        self.paper_trades.clear()
        self._equity_curve.clear()
        logger.info(f"[PAPER] reset edildi, yeni bakiye: {self.paper_balance:.2f}")

    # ============================================================
    # DÖNGÜLER
    # ============================================================
    async def _price_loop(self):
        while not self._stop_event.is_set():
            try:
                if self.symbols:
                    tickers = await self.exchange.get_tickers(self.symbols)
                    for sym, t in (tickers or {}).items():
                        self.live_prices[sym] = t["last"]
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[PAPER] price loop hatasi: {e}")
            await asyncio.sleep(Config.PRICE_UPDATE_INTERVAL)

    async def _strategy_loop(self):
        while not self._stop_event.is_set():
            try:
                tasks = []
                for s in self.strategies.values():
                    if not s.enabled:
                        continue
                    for sym in s.symbols:
                        if sym in self.symbols:
                            tasks.append(self._process_strategy(s, sym))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[PAPER] strategy loop hatasi: {e}")
            await asyncio.sleep(Config.STRATEGY_LOOP_INTERVAL)

    async def _process_strategy(self, strategy: BaseStrategy, symbol: str):
        try:
            ohlcv = await self.exchange.get_ohlcv(symbol, strategy.timeframe, limit=300)
            if not ohlcv or len(ohlcv) < strategy.min_candles:
                return
            signal = await strategy.generate_signal(symbol, ohlcv)
            if not signal or signal.type == SignalType.HOLD:
                return

            live = self.live_prices.get(symbol) or signal.price
            if live <= 0:
                return

            # Pozisyon yönetimi: spot mantığı
            has_pos = symbol in self.paper_positions
            if signal.type == SignalType.BUY and not has_pos:
                # Position size: paper bakiyesinin %5'i (Config.MAX_POSITION_SIZE_PCT)
                value = self.paper_balance * Config.MAX_POSITION_SIZE_PCT
                if value < Config.MIN_ORDER_VALUE:
                    return
                amount = value / live
                await self._execute_paper_order(
                    symbol=symbol, side="buy", amount=amount, price=live,
                    strategy=strategy.name, reason=signal.reason,
                )
            elif signal.type == SignalType.SELL and has_pos:
                pos = self.paper_positions[symbol]
                await self._execute_paper_order(
                    symbol=symbol, side="sell", amount=pos["amount"], price=live,
                    strategy=strategy.name, reason=signal.reason,
                )
        except Exception as e:
            logger.error(f"[PAPER] strategy process hatasi [{strategy.name}/{symbol}]: {e}")

    async def _sl_tp_monitor(self):
        """Paper pozisyonların SL/TP kontrolü."""
        while not self._stop_event.is_set():
            try:
                for symbol, pos in list(self.paper_positions.items()):
                    price = self.live_prices.get(symbol)
                    if not price or price <= 0:
                        continue
                    triggered = None
                    if price <= pos["stop_loss"]:
                        triggered = "STOP_LOSS"
                    elif price >= pos["take_profit"]:
                        triggered = "TAKE_PROFIT"
                    if triggered:
                        logger.info(f"[PAPER] {symbol} {triggered} @ {price:.4f}")
                        await self._execute_paper_order(
                            symbol=symbol, side="sell", amount=pos["amount"],
                            price=price, strategy=pos["strategy"],
                            reason=f"{triggered} tetiklendi",
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[PAPER] SL/TP monitor hatasi: {e}")
            await asyncio.sleep(Config.PRICE_UPDATE_INTERVAL)

    async def _equity_snapshot_loop(self):
        """Her 10 saniyede bir equity snapshot al."""
        while not self._stop_event.is_set():
            try:
                eq = self._compute_equity()
                self._equity_curve.append({
                    "time": int(_utcnow().timestamp()),
                    "value": round(eq, 4),
                })
                # bounded tut
                if len(self._equity_curve) > self.MAX_EQUITY_POINTS:
                    self._equity_curve = self._equity_curve[-self.MAX_EQUITY_POINTS:]
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[PAPER] equity snapshot hatasi: {e}")
            await asyncio.sleep(10)

    # ============================================================
    # ÖZET / METRIK
    # ============================================================
    def _compute_equity(self) -> float:
        """Paper bakiye + açık pozisyonların mark-to-market değeri."""
        eq = self.paper_balance
        for sym, pos in self.paper_positions.items():
            live = self.live_prices.get(sym, pos["entry_price"])
            eq += pos["amount"] * live
        return eq

    def get_paper_summary(self) -> Dict[str, Any]:
        """Paper trader özet durumu."""
        eq = self._compute_equity()
        total_pnl = eq - self.initial_balance
        pnl_pct = (total_pnl / self.initial_balance * 100) if self.initial_balance > 0 else 0.0

        positions_list = []
        for sym, pos in self.paper_positions.items():
            live = self.live_prices.get(sym, pos["entry_price"])
            upnl = (live - pos["entry_price"]) * pos["amount"]
            upnl_pct = ((live - pos["entry_price"]) / pos["entry_price"] * 100) if pos["entry_price"] > 0 else 0.0
            positions_list.append({
                "symbol": sym,
                "side": pos["side"],
                "amount": pos["amount"],
                "entry_price": pos["entry_price"],
                "current_price": live,
                "unrealized_pnl": round(upnl, 4),
                "unrealized_pnl_pct": round(upnl_pct, 4),
                "stop_loss": pos["stop_loss"],
                "take_profit": pos["take_profit"],
                "strategy": pos["strategy"],
                "opened_at": pos["entry_time"].isoformat() if isinstance(pos["entry_time"], datetime) else pos["entry_time"],
            })

        return {
            "enabled": self.enabled,
            "initial_balance": self.initial_balance,
            "paper_balance": round(self.paper_balance, 4),
            "equity": round(eq, 4),
            "total_pnl": round(total_pnl, 4),
            "total_pnl_pct": round(pnl_pct, 4),
            "open_positions": positions_list,
            "open_positions_count": len(positions_list),
            "total_trades": len(self.paper_trades),
            "symbols": self.symbols,
            "live_prices": self.live_prices,
        }

    def get_paper_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Paper trade geçmişi (in-memory, DB'den ayrıca filtrelenebilir)."""
        return self.paper_trades[-limit:][::-1]

    def get_equity_curve(self) -> List[Dict[str, Any]]:
        return list(self._equity_curve)

    # ============================================================
    # NOTIFICATION
    # ============================================================
    def add_notification_callback(self, cb: Callable):
        self.notification_callbacks.append(cb)

    async def _notify(self, data: Dict[str, Any]):
        for cb in self.notification_callbacks:
            try:
                r = cb(data)
                if asyncio.iscoroutine(r):
                    await r
            except Exception as e:
                logger.error(f"[PAPER] notify hatasi: {e}")


# Singleton
import threading as _threading
_paper_instance: Optional[PaperTrader] = None
_paper_lock = _threading.Lock()


def get_paper_trader() -> PaperTrader:
    """Thread-safe singleton accessor for PaperTrader."""
    global _paper_instance
    if _paper_instance is None:
        with _paper_lock:
            if _paper_instance is None:
                _paper_instance = PaperTrader()
    return _paper_instance
