"""
Ana Trading Motoru
- Tüm stratejileri yönetir
- Asyncio ile paralel çalışır
- Sinyal -> Risk kontrol -> Emir akışı
- Başlat / Durdur / Duraklatma
"""

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


def _utcnow() -> datetime:
    """UTC timestamp - _utcnow() deprecated in Python 3.12+."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

from config import Config
from database import db

from .exchange import Exchange
from .portfolio import Portfolio, Position
from .risk_manager import RiskManager
from .strategies import STRATEGY_REGISTRY
from .strategies.base import BaseStrategy, Signal, SignalType

logger = logging.getLogger(__name__)


class BotStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


class Trader:
    """AgentTrader ana motoru"""

    def __init__(self):
        self.exchange = Exchange()
        self.portfolio = Portfolio()
        self.risk_manager = RiskManager()

        self.strategies: Dict[str, BaseStrategy] = {}
        self.symbols: List[str] = list(Config.DEFAULT_SYMBOLS)

        self.status: BotStatus = BotStatus.STOPPED
        self._tasks: List[asyncio.Task] = []
        # asyncio primitif'leri lazy oluşturulur - modül yüklenme sırasında
        # event loop olmayabilir (Python 3.10+ DeprecationWarning)
        self._lock: Optional[asyncio.Lock] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._pause_event: Optional[asyncio.Event] = None

        # Sembol bazlı asyncio lock haritası (race condition: iki strateji aynı
        # sembol için eş zamanlı pozisyon açmaya çalışabilir). Lazy oluşturulur.
        self._symbol_locks: Dict[str, asyncio.Lock] = {}

        self.live_prices: Dict[str, float] = {}
        self.notification_callbacks: List[Callable] = []
        self._scanner_task: Optional[asyncio.Task] = None

        self._initialize_strategies()

    def _ensure_async_primitives(self):
        """Event loop içinde asyncio primitive'leri hazırla."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        if self._pause_event is None:
            self._pause_event = asyncio.Event()
            self._pause_event.set()  # Default: running (pause değil)

    # ============================================================
    # STRATEJİLER
    # ============================================================
    def _initialize_strategies(self):
        """Config'deki stratejileri initialize et"""
        for name, cfg in Config.STRATEGY_CONFIGS.items():
            if name in STRATEGY_REGISTRY:
                self.strategies[name] = STRATEGY_REGISTRY[name](cfg)
                logger.info(
                    f"Strateji yuklendi: {name} (enabled={cfg.get('enabled')})"
                )

    def toggle_strategy(self, name: str, enabled: Optional[bool] = None) -> bool:
        """Stratejiyi aç/kapat"""
        if name not in self.strategies:
            raise ValueError(f"Strateji bulunamadi: {name}")
        return self.strategies[name].toggle(enabled)

    def get_strategies_info(self) -> List[Dict[str, Any]]:
        """Tüm stratejilerin durumu"""
        return [s.get_info() for s in self.strategies.values()]

    # ============================================================
    # SEMBOLLER
    # ============================================================
    def add_symbol(self, symbol: str):
        sym = symbol.upper()
        if "/" not in sym:
            # BTCUSDT -> BTC/USDT normalize
            if sym.endswith("USDT"):
                sym = sym[:-4] + "/USDT"
        if sym not in self.symbols:
            self.symbols.append(sym)
            logger.info(f"Sembol eklendi: {sym}")

    def remove_symbol(self, symbol: str):
        sym = symbol.upper()
        if "/" not in sym and sym.endswith("USDT"):
            sym = sym[:-4] + "/USDT"
        if sym in self.symbols:
            self.symbols.remove(sym)
            logger.info(f"Sembol cikarildi: {sym}")

    # ============================================================
    # BOT KONTROLÜ
    # ============================================================
    async def start(self):
        """Bot'u başlat"""
        self._ensure_async_primitives()
        async with self._lock:
            if self.status == BotStatus.RUNNING:
                logger.warning("Bot zaten calisiyor")
                return
            self.status = BotStatus.STARTING
            logger.info("Bot baslatiliyor...")

        try:
            await self.exchange.connect()

            # İlk bakiye snapshot'u
            bal = await self.exchange.get_balance()
            self.portfolio.update_balance(bal)
            self.risk_manager.update_balance_snapshot(bal.get("usdt_value", 0))

            self._stop_event.clear()
            self._pause_event.set()

            # Task'ları oluştur
            self._tasks = [
                asyncio.create_task(self._price_updater(), name="price_updater"),
                asyncio.create_task(self._portfolio_updater(), name="portfolio_updater"),
                asyncio.create_task(self._strategy_loop(), name="strategy_loop"),
                asyncio.create_task(self._sl_tp_monitor(), name="sl_tp_monitor"),
            ]

            self.status = BotStatus.RUNNING
            db.set_state("bot_status", self.status.value)
            logger.info("Bot AKTIF - trading baslatildi")
            await self._notify({"event": "bot_started", "time": _utcnow().isoformat()})
        except Exception as e:
            logger.exception(f"Bot baslatma hatasi: {e}")
            self.status = BotStatus.ERROR
            raise

    async def stop(self):
        """Bot'u durdur"""
        self._ensure_async_primitives()
        async with self._lock:
            if self.status == BotStatus.STOPPED:
                return
            self.status = BotStatus.STOPPING
            logger.info("Bot durduruluyor...")

        self._stop_event.set()
        self._pause_event.set()  # pause varsa serbest bırak ki task'lar çıkabilsin

        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []

        # Scanner (always-on) ayrı yaşıyordu, onu da durdur
        if self._scanner_task and not self._scanner_task.done():
            self._scanner_task.cancel()
            try:
                await self._scanner_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Scanner stop cleanup: {e}")
        self._scanner_task = None

        await self.exchange.close()

        self.status = BotStatus.STOPPED
        db.set_state("bot_status", self.status.value)
        logger.info("Bot DURDURULDU")
        await self._notify({"event": "bot_stopped", "time": _utcnow().isoformat()})

    async def pause(self):
        """Bot'u duraklat (çalışır durumda kalır ama trade açmaz)"""
        if self.status != BotStatus.RUNNING:
            return
        self._ensure_async_primitives()
        self._pause_event.clear()
        self.status = BotStatus.PAUSED
        db.set_state("bot_status", self.status.value)
        logger.info("Bot DURAKLATILDI")
        await self._notify({"event": "bot_paused"})

    async def resume(self):
        """Duraklatma kalksın"""
        if self.status != BotStatus.PAUSED:
            return
        self._ensure_async_primitives()
        self._pause_event.set()
        self.status = BotStatus.RUNNING
        db.set_state("bot_status", self.status.value)
        logger.info("Bot devam ediyor")
        await self._notify({"event": "bot_resumed"})

    # ============================================================
    # ANA DÖNGÜLER
    # ============================================================
    async def _price_updater(self):
        """Fiyatları periyodik olarak güncelle"""
        logger.info("Price updater basladi")
        consecutive_errors = 0
        while not self._stop_event.is_set():
            try:
                if not self.symbols:
                    await asyncio.sleep(Config.PRICE_UPDATE_INTERVAL)
                    continue
                tickers = await self.exchange.get_tickers(self.symbols)
                if tickers:
                    for sym, t in tickers.items():
                        self.live_prices[sym] = t["last"]
                    # Pozisyonları güncelle
                    self.portfolio.update_prices(self.live_prices)
                    # Fiyat alarmlarını kontrol et (BLOK 7 - dashboard alarmı)
                    try:
                        from web.routes.api import check_price_alarms
                        triggered = check_price_alarms(self.live_prices)
                        for alarm in triggered:
                            logger.info(
                                f"[ALARM] {alarm['symbol']} {alarm['direction']} "
                                f"{alarm['price']} tetiklendi @ {alarm.get('triggered_price')}"
                            )
                            await self._notify({
                                "event": "price_alarm",
                                "alarm": alarm,
                            })
                    except Exception as e:
                        logger.debug(f"Alarm check hatasi: {e}")
                    await self._notify({
                        "event": "prices_updated",
                        "prices": self.live_prices,
                    })
                consecutive_errors = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Price updater hatasi ({consecutive_errors}): {e}")
                # Exponential backoff at repeated failures (cap 30s)
                if consecutive_errors > 3:
                    await asyncio.sleep(min(30, 2 ** consecutive_errors))
            await asyncio.sleep(Config.PRICE_UPDATE_INTERVAL)
        logger.info("Price updater durdu")

    async def _sl_tp_monitor(self):
        """Açık pozisyonların SL/TP seviyelerini izle ve tetikle.

        Trailing stop entegrasyonu: her tick'te
        `risk_manager.update_trailing_stop` çağrılır. Trailing değer
        statik SL'den daha sıkı ise (long için: trailing > statik) onu
        override eder. Pozisyon kapatıldığında trailing kaydı temizlenir.
        """
        logger.info("SL/TP monitor basladi")
        while not self._stop_event.is_set():
            try:
                await self._pause_event.wait()
                for symbol, position in list(self.portfolio.positions.items()):
                    price = self.live_prices.get(symbol)
                    if not price or price <= 0:
                        continue
                    sl = getattr(position, "stop_loss", None)
                    tp = getattr(position, "take_profit", None)

                    # Trailing stop güncellemesi - statik SL'i daha sıkı
                    # seviyeyle override edebilir.
                    trailing_sl = self.risk_manager.update_trailing_stop(
                        symbol, price, position.side,
                    )
                    if trailing_sl:
                        if position.side == "long":
                            # Long'ta trailing yüksekse (daha agresif koruma) onu kullan
                            sl = max(sl, trailing_sl) if sl else trailing_sl
                        elif position.side == "short":
                            # Short'ta trailing düşükse daha sıkı koruma
                            sl = min(sl, trailing_sl) if sl else trailing_sl

                    triggered = None
                    if position.side == "long":
                        if sl and price <= sl:
                            triggered = ("STOP_LOSS", sl)
                        elif tp and price >= tp:
                            triggered = ("TAKE_PROFIT", tp)
                    elif position.side == "short":
                        if sl and price >= sl:
                            triggered = ("STOP_LOSS", sl)
                        elif tp and price <= tp:
                            triggered = ("TAKE_PROFIT", tp)
                    if triggered:
                        reason, level = triggered
                        logger.warning(f"[SL/TP] {symbol} {reason} tetiklendi @ {price:.4f} (hedef: {level:.4f})")
                        try:
                            order = await self.exchange.place_order(symbol, "sell" if position.side == "long" else "buy", position.amount)
                            self.portfolio.close_position(symbol, price)
                            # Pozisyon kapandı -> trailing kaydını sil
                            self.risk_manager.clear_trailing_stop(symbol)
                            await self._notify({"event": "sl_tp_triggered", "symbol": symbol, "reason": reason, "price": price, "order": order})
                        except Exception as e:
                            logger.error(f"SL/TP emri hatasi {symbol}: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"SL/TP monitor hatasi: {e}")
            await asyncio.sleep(Config.PRICE_UPDATE_INTERVAL)
        logger.info("SL/TP monitor durdu")

    async def _portfolio_updater(self):
        """Bakiyeyi periyodik olarak yenile"""
        logger.info("Portfolio updater basladi")
        while not self._stop_event.is_set():
            try:
                bal = await self.exchange.get_balance()
                self.portfolio.update_balance(bal)
                self.risk_manager.update_balance_snapshot(bal.get("usdt_value", 0))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Portfolio updater hatasi: {e}")
            await asyncio.sleep(15)
        logger.info("Portfolio updater durdu")

    async def _strategy_loop(self):
        """Tüm stratejileri paralel olarak çalıştır"""
        logger.info("Strategy loop basladi")
        while not self._stop_event.is_set():
            try:
                # Pause ise bekle
                await self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                tasks = []
                for s in self.strategies.values():
                    if not s.enabled:
                        continue
                    for symbol in s.symbols:
                        if symbol in self.symbols:
                            tasks.append(
                                self._process_strategy_for_symbol(s, symbol)
                            )
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Strategy loop hatasi: {e}")
            await asyncio.sleep(Config.STRATEGY_LOOP_INTERVAL)
        logger.info("Strategy loop durdu")

    async def start_scanner(self):
        """Bot durdurulsa bile sinyal taramasını başlat (grafik gösterimi için)."""
        if self._scanner_task and not self._scanner_task.done():
            return
        self._scanner_task = asyncio.create_task(
            self._always_on_scanner(), name="signal_scanner"
        )
        logger.info("Signal scanner baslatildi")

    async def _always_on_scanner(self):
        """Her zaman çalışan sinyal tarayıcı — bot durumundan bağımsız.
        Bot STOPPED iken sinyalleri DB'ye kaydeder ama emir vermez.
        Bot RUNNING iken strategy_loop zaten çalıştığından sadece sinyal kaydeder."""
        logger.info("Always-on scanner aktif")
        while True:
            try:
                # Bot RUNNING ise strategy_loop zaten çalışıyor, çift çalıştırma
                if self.status == BotStatus.RUNNING:
                    await asyncio.sleep(Config.STRATEGY_LOOP_INTERVAL)
                    continue
                # Exchange bağlı değilse atla
                if not self.exchange._client:
                    await asyncio.sleep(30)
                    continue
                tasks = []
                for s in self.strategies.values():
                    if not s.enabled:
                        continue
                    for symbol in s.symbols:
                        if symbol in self.symbols:
                            tasks.append(self._process_strategy_for_symbol(s, symbol))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Always-on scanner hatasi: {e}")
            await asyncio.sleep(Config.STRATEGY_LOOP_INTERVAL)
        logger.info("Always-on scanner durdu")

    async def _process_strategy_for_symbol(
        self, strategy: BaseStrategy, symbol: str
    ):
        """Bir strateji-sembol çifti için sinyal al ve işle"""
        try:
            ohlcv = await self.exchange.get_ohlcv(
                symbol, strategy.timeframe, limit=200
            )
            if not ohlcv or len(ohlcv) < strategy.min_candles:
                return
            # DB'ye cachele
            try:
                db.save_ohlcv(symbol, strategy.timeframe, ohlcv[-50:])
            except Exception as e:
                logger.debug(f"OHLCV cache hatasi: {e}")

            signal = await strategy.generate_signal(symbol, ohlcv)
            if signal and signal.type != SignalType.HOLD:
                logger.info(
                    f"[{strategy.name}] {signal.type.value.upper()} {symbol} "
                    f"@ {signal.price} - {signal.reason}"
                )
                strategy.record_signal(signal)
                # Her sinyali DB'ye kaydet (bakiye/bot durumundan bağımsız)
                sig_id = db.save_signal(
                    symbol=symbol, strategy=strategy.name,
                    signal_type=signal.type.value, price=signal.price,
                    strength=signal.strength, reason=signal.reason, executed=False,
                )
                # WebSocket üzerinden bildir (grafik markerleri için)
                await self._notify({
                    "event": "signal",
                    "signal": {**signal.to_dict(), "db_id": sig_id, "strategy": strategy.name},
                })
                # Yalnızca bot RUNNING durumundaysa işlem yap
                if self.status == BotStatus.RUNNING:
                    executed = await self._execute_signal(strategy, signal)
                    if executed:
                        db.mark_signal_executed(sig_id)
        except Exception as e:
            logger.error(f"Sinyal isleme hatasi [{strategy.name}/{symbol}]: {e}")

    async def _execute_signal(self, strategy: BaseStrategy, signal: Signal) -> bool:
        """Sinyali risk kontrolünden geçirip emir ver. True dönerse trade verildi."""
        if self.status != BotStatus.RUNNING:
            return False

        symbol = signal.symbol
        side = signal.type.value  # buy / sell

        # SEMBOL LOCK: İki strateji aynı sembol için eş zamanlı çalışırsa
        # has_position() ile add_position() arası await noktaları race yaratır.
        # Sembol bazlı lock ile her sembol için pozisyon işlemlerini serileştiriyoruz.
        if symbol not in self._symbol_locks:
            self._symbol_locks[symbol] = asyncio.Lock()
        async with self._symbol_locks[symbol]:
            # Live price: signal.price son kapanan mumun close'u (15dk geride olabilir).
            # Market emri live fiyatla dolar, SL/TP hesaplaması için de güncel değer şart.
            live = self.live_prices.get(symbol)
            price = live if (live and live > 0) else signal.price

            if price is None or price <= 0:
                logger.warning(f"Gecersiz sinyal fiyati: {symbol} @ {price}")
                return False

            # Mevcut pozisyon kontrolü
            has_pos = self.portfolio.has_position(symbol)

            # Aynı yönde tekrar açmayı önle
            if has_pos:
                pos = self.portfolio.get_position(symbol)
                if (pos.side == "long" and side == "buy") or (
                    pos.side == "short" and side == "sell"
                ):
                    logger.debug(f"{symbol}: ayni yonde pozisyon var, atlandi")
                    return False
                # Zıt yönlü sinyal -> pozisyonu kapat (spot'ta long pozisyona sell sinyali = kapat)
                await self._close_position(symbol, price, strategy.name)
                return True

            # SPOT hesap için: sell sinyali ama pozisyon yoksa IGNORE
            if side == "sell" and not has_pos:
                logger.debug(f"{symbol}: sell sinyali ama acik pozisyon yok, atlandi (spot)")
                return False

            balance_usdt = self.portfolio.free_usdt or self.portfolio.total_usdt
            if balance_usdt <= 0:
                logger.warning(f"Yetersiz USDT bakiye, sinyal atlandi: {symbol}")
                return False
            proposed = self.risk_manager.calculate_position_size(balance_usdt, price)
            if proposed <= 0:
                logger.warning(f"Hesaplanan pozisyon boyutu 0: {symbol} @ {price}")
                return False

            risk_check = self.risk_manager.validate_order(
                symbol=symbol,
                side=side,
                proposed_amount=proposed,
                price=price,
                balance_usdt=balance_usdt,
                open_positions=self.portfolio.open_positions_count(),
                current_balance=self.portfolio.total_usdt,
            )

            if not risk_check.approved:
                logger.warning(
                    f"RISK RED - {symbol} {side}: {risk_check.reason}"
                )
                return False

            amount = risk_check.adjusted_amount or proposed

            # Emir
            order = await self.exchange.place_order(
                symbol=symbol, side=side, amount=amount, order_type="market"
            )
            if not order:
                logger.error(f"Emir basarisiz: {symbol} {side} {amount}")
                return False

            filled_price = float(order.get("average") or order.get("price") or price)
            fee = 0.0
            try:
                if order.get("fee"):
                    fee = float(order["fee"].get("cost") or 0)
            except Exception:
                pass

            # Pozisyon kaydı
            pos_side = "long" if side == "buy" else "short"
            position = Position(
                symbol=symbol,
                side=pos_side,
                amount=amount,
                entry_price=filled_price,
                current_price=filled_price,
                strategy=strategy.name,
                stop_loss=risk_check.stop_loss,
                take_profit=risk_check.take_profit,
            )
            self.portfolio.add_position(position)

            # DB kayıt
            try:
                db.add_trade(
                    {
                        "order_id": order.get("id"),
                        "symbol": symbol,
                        "side": side,
                        "amount": amount,
                        "price": filled_price,
                        "cost": amount * filled_price,
                        "fee": fee,
                        "pnl": 0.0,
                        "pnl_pct": 0.0,
                        "strategy": strategy.name,
                        "status": "filled",
                        "order_type": "market",
                        "note": signal.reason,
                    }
                )
            except Exception as e:
                logger.error(f"Trade DB kayit hatasi: {e}")

            await self._notify({
                "event": "trade_executed",
                "trade": {
                    "symbol": symbol,
                    "side": side,
                    "amount": amount,
                    "price": filled_price,
                    "strategy": strategy.name,
                    "reason": signal.reason,
                },
            })
            return True

    async def _close_position(self, symbol: str, price: float, strategy_name: str):
        """Pozisyonu kapat"""
        pos = self.portfolio.get_position(symbol)
        if not pos:
            return

        side = "sell" if pos.side == "long" else "buy"
        order = await self.exchange.place_order(
            symbol=symbol, side=side, amount=pos.amount, order_type="market"
        )
        if not order:
            logger.error(f"Pozisyon kapatma basarisiz: {symbol}")
            return

        exit_price = float(order.get("average") or order.get("price") or price)
        closed = self.portfolio.close_position(symbol, exit_price)
        if not closed:
            return

        # Pozisyon kapandıktan sonra trailing SL kaydını temizle
        try:
            self.risk_manager.clear_trailing_stop(symbol)
        except Exception:
            pass

        try:
            db.add_trade(
                {
                    "order_id": order.get("id"),
                    "symbol": symbol,
                    "side": side,
                    "amount": pos.amount,
                    "price": exit_price,
                    "cost": pos.amount * exit_price,
                    "pnl": closed["pnl"],
                    "pnl_pct": closed["pnl_pct"],
                    "strategy": strategy_name,
                    "status": "filled",
                    "order_type": "market",
                    "note": f"Pozisyon kapatildi ({closed['pnl_pct']:.2f}%)",
                }
            )
        except Exception as e:
            logger.error(f"Close trade DB kayit hatasi: {e}")

        await self._notify({
            "event": "position_closed",
            "trade": closed,
        })

    # ============================================================
    # MANUEL EMİR
    # ============================================================
    async def manual_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Dashboard üzerinden manuel emir"""
        await self.exchange.connect()
        ticker = await self.exchange.get_ticker(symbol)
        if not ticker:
            return {"success": False, "error": "Ticker alinamadi"}

        exec_price = price or ticker["last"]
        order = await self.exchange.place_order(
            symbol=symbol,
            side=side.lower(),
            amount=amount,
            price=price,
            order_type=order_type,
        )
        if not order:
            return {"success": False, "error": "Emir olusturulamadi"}

        try:
            db.add_trade(
                {
                    "order_id": order.get("id"),
                    "symbol": symbol,
                    "side": side.lower(),
                    "amount": amount,
                    "price": exec_price,
                    "cost": amount * exec_price,
                    "strategy": "manual",
                    "status": "filled",
                    "order_type": order_type,
                    "note": "Manuel emir",
                }
            )
        except Exception as e:
            logger.error(f"Manual trade kayit hatasi: {e}")

        return {"success": True, "order": order}

    # ============================================================
    # NOTIFICATION
    # ============================================================
    def add_notification_callback(self, callback: Callable):
        self.notification_callbacks.append(callback)

    async def _notify(self, data: Dict[str, Any]):
        """Callback'leri çağır (WebSocket, Telegram, vb.)"""
        for cb in self.notification_callbacks:
            try:
                result = cb(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Notification callback hatasi: {e}")

    # ============================================================
    # DURUM
    # ============================================================
    def get_status(self) -> Dict[str, Any]:
        """Bot durumu"""
        return {
            "status": self.status.value,
            "symbols": self.symbols,
            "strategies": {
                name: {"enabled": s.enabled, "timeframe": s.timeframe}
                for name, s in self.strategies.items()
            },
            "active_strategies": sum(
                1 for s in self.strategies.values() if s.enabled
            ),
            "portfolio": self.portfolio.get_summary(),
            "risk": self.risk_manager.get_risk_summary(self.portfolio.total_usdt),
            "live_prices": self.live_prices,
            "uptime_since": db.get_state("bot_started_at"),
        }


# Global trader instance (lazy + thread-safe)
import threading as _threading
_trader_instance: Optional[Trader] = None
_trader_lock = _threading.Lock()


def get_trader() -> Trader:
    """Thread-safe singleton accessor."""
    global _trader_instance
    if _trader_instance is None:
        with _trader_lock:
            # Double-check locking
            if _trader_instance is None:
                _trader_instance = Trader()
    return _trader_instance
