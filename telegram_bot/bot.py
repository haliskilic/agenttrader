"""
Telegram Bot - Kontrol ve Bildirim
- python-telegram-bot v21 kullanır (async)
- Sadece TELEGRAM_CHAT_ID izinli
- Trade bildirimleri, /komutlar
"""

import asyncio
import logging
from datetime import datetime, time as dtime, timezone
from typing import Any, Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from config import Config
from database import db

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram bot sarmalayıcı"""

    def __init__(self, trader=None):
        self.token = Config.TELEGRAM_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.app: Optional[Application] = None
        self.trader = trader
        self.enabled = bool(self.token and self.chat_id)
        self._task: Optional[asyncio.Task] = None

        # Fiyat alarmları: { chat_id_str: [ {id, symbol, price, direction} ] }
        # Format: symbol uppercase, "/" içermez (BTCUSDT formatında).
        self._price_alerts: Dict[str, List[Dict[str, Any]]] = {}
        self._alert_id_counter: int = 0
        # Son kontrol edilen fiyatlar — iki tetiklemeyi ardışık önlemek için
        # trader'ın live_prices'ı zaten asenkron akar, bu alanı cache olarak
        # kullanıyoruz.
        self._last_checked_prices: Dict[str, float] = {}

    def set_trader(self, trader):
        self.trader = trader

    # ============================================================
    # SECURITY
    # ============================================================
    def _is_authorized(self, update: Update) -> bool:
        """Sadece izinli chat ID"""
        if not self.chat_id:
            return False
        return str(update.effective_chat.id) == str(self.chat_id)

    async def _unauthorized(self, update: Update):
        await update.message.reply_text("Yetkiniz yok.")
        logger.warning(
            f"Yetkisiz Telegram erisimi: chat_id={update.effective_chat.id}"
        )

    # ============================================================
    # COMMAND HANDLERS
    # ============================================================
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return await self._unauthorized(update)

        text = (
            f"*AgentTrader v{Config.VERSION}*\n\n"
            "OKX crypto trading bot kontrol paneli.\n\n"
            "Komutlar:\n"
            "/status - Bot durumu\n"
            "/balance - Anlik bakiye\n"
            "/positions - Acik pozisyonlar\n"
            "/pnl - Bugunku PnL\n"
            "/trades - Son 5 trade\n"
            "/startbot - Botu baslat\n"
            "/stopbot - Botu durdur\n"
            "/pausebot - Botu duraklat\n"
            "/setpair BTCUSDT - Sembol ekle\n"
            "/alarm BTCUSDT 90000 above - Fiyat alarmi\n"
            "/alarmlar - Alarm listesi\n"
            "/alarmsil ID - Alarm sil\n"
            "/papermode on|off - Paper trading\n"
            "/paperstatus - Paper bakiye\n"
            "/help - Yardim"
        )
        keyboard = [
            [
                InlineKeyboardButton("Durum", callback_data="status"),
                InlineKeyboardButton("Bakiye", callback_data="balance"),
            ],
            [
                InlineKeyboardButton("Baslat", callback_data="start"),
                InlineKeyboardButton("Duraklat", callback_data="pause"),
                InlineKeyboardButton("Durdur", callback_data="stop"),
            ],
            [
                InlineKeyboardButton("PnL", callback_data="pnl"),
                InlineKeyboardButton("Trades", callback_data="trades"),
            ],
        ]
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.cmd_start(update, context)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not self.trader:
            await update.message.reply_text("Trader hazir degil")
            return
        st = self.trader.get_status()
        active = st.get("active_strategies", 0)
        total = len(self.trader.strategies)
        text = (
            f"*Bot Durumu*\n\n"
            f"Durum: `{st['status'].upper()}`\n"
            f"Aktif strateji: `{active}/{total}`\n"
            f"Izlenen sembol: `{len(st['symbols'])}`\n"
            f"Toplam USDT: `{st['portfolio']['total_usdt']:.4f}`\n"
            f"Acik pozisyon: `{st['portfolio']['open_positions_count']}`"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not self.trader:
            await update.message.reply_text("Trader hazir degil")
            return
        try:
            bal = await self.trader.exchange.get_balance()
            lines = [f"*Bakiye*\n"]
            lines.append(f"Toplam USDT degeri: `{bal.get('usdt_value', 0):.4f}`\n")
            for cur, amount in bal.get("total", {}).items():
                free = bal.get("free", {}).get(cur, 0)
                lines.append(f"  {cur}: `{amount:.8f}` (serbest: `{free:.8f}`)")
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            # Iç exception detayını kullanıcıya verme - loglayıp generic mesaj göster
            logger.error(f"cmd_balance hatasi: {e}")
            await update.message.reply_text("Bir hata olustu, lutfen tekrar deneyin.")

    async def cmd_positions(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not self.trader:
            return
        positions = self.trader.portfolio.open_positions_list()
        if not positions:
            await update.message.reply_text("Acik pozisyon yok")
            return
        lines = ["*Acik Pozisyonlar*\n"]
        for p in positions:
            pnl_sign = "+" if p["unrealized_pnl"] >= 0 else ""
            lines.append(
                f"- `{p['symbol']}` {p['side'].upper()} "
                f"{p['amount']} @ {p['entry_price']:.4f}\n"
                f"  PnL: `{pnl_sign}{p['unrealized_pnl']:.4f}` "
                f"({p['unrealized_pnl_pct']:.2f}%)"
            )
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )

    async def cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not self.trader:
            return
        daily = db.get_daily_pnl()
        stats = db.get_trade_stats(days=30)
        unr = self.trader.portfolio.total_unrealized_pnl
        text = (
            f"*PnL Raporu*\n\n"
            f"Bugunku: `{daily:+.4f}` USDT\n"
            f"Gerceklesmemis: `{unr:+.4f}` USDT\n"
            f"30 Gunluk Toplam: `{stats['total_pnl']:+.4f}`\n"
            f"Win Rate: `{stats['win_rate']:.2f}%`\n"
            f"Toplam Trade: `{stats['total_trades']}`\n"
            f"En Iyi: `{stats['best_trade']:+.4f}`\n"
            f"En Kotu: `{stats['worst_trade']:+.4f}`"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        trades = db.get_trades(limit=5)
        if not trades:
            await update.message.reply_text("Trade yok")
            return
        lines = ["*Son 5 Trade*\n"]
        for t in trades:
            sign = "+" if (t.get("pnl") or 0) >= 0 else ""
            lines.append(
                f"- `{t['symbol']}` {t['side'].upper()} "
                f"{t['amount']} @ {t['price']:.4f}\n"
                f"  Strateji: {t['strategy']}, PnL: `{sign}{t.get('pnl') or 0:.4f}`"
            )
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )

    async def cmd_startbot(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not self.trader:
            return
        try:
            await self.trader.start()
            await update.message.reply_text("Bot baslatildi")
        except Exception as e:
            logger.error(f"cmd_startbot hatasi: {e}")
            await update.message.reply_text("Bir hata olustu, lutfen tekrar deneyin.")

    async def cmd_stopbot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not self.trader:
            return
        try:
            await self.trader.stop()
            await update.message.reply_text("Bot durduruldu")
        except Exception as e:
            logger.error(f"cmd_stopbot hatasi: {e}")
            await update.message.reply_text("Bir hata olustu, lutfen tekrar deneyin.")

    async def cmd_pausebot(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not self.trader:
            return
        try:
            if self.trader.status.value == "paused":
                await self.trader.resume()
                await update.message.reply_text("Bot devam ediyor")
            else:
                await self.trader.pause()
                await update.message.reply_text("Bot duraklatildi")
        except Exception as e:
            logger.error(f"cmd_pausebot hatasi: {e}")
            await update.message.reply_text("Bir hata olustu, lutfen tekrar deneyin.")

    async def cmd_setpair(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not context.args:
            await update.message.reply_text("Kullanim: /setpair BTCUSDT")
            return
        sym = context.args[0].upper()
        if self.trader:
            self.trader.add_symbol(sym)
            await update.message.reply_text(
                f"Sembol eklendi: {sym}. "
                f"Izlenen: {', '.join(self.trader.symbols)}"
            )

    # ============================================================
    # FIYAT ALARMI (BLOK 5A)
    # ============================================================
    @staticmethod
    def _normalize_symbol(sym: str) -> str:
        """'BTCUSDT' veya 'btc/usdt' -> 'BTC/USDT'."""
        s = sym.upper().replace(" ", "")
        if "/" in s:
            return s
        if s.endswith("USDT"):
            return s[:-4] + "/USDT"
        if s.endswith("USDC"):
            return s[:-4] + "/USDC"
        return s

    async def cmd_alarm(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/alarm SYMBOL PRICE above|below

        Örnek:
            /alarm BTCUSDT 90000 above
            /alarm BTCUSDT 80000 below
        """
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not context.args or len(context.args) < 3:
            await update.message.reply_text(
                "Kullanim: /alarm BTCUSDT 90000 above\n"
                "         /alarm BTCUSDT 80000 below"
            )
            return
        try:
            symbol = self._normalize_symbol(context.args[0])
            price = float(context.args[1])
            direction = context.args[2].lower()
            if direction not in ("above", "below"):
                raise ValueError("direction 'above' veya 'below' olmali")
            if price <= 0:
                raise ValueError("price > 0 olmali")
        except Exception as e:
            await update.message.reply_text(f"Gecersiz parametre: {e}")
            return

        chat_key = str(update.effective_chat.id)
        self._alert_id_counter += 1
        alarm = {
            "id": self._alert_id_counter,
            "symbol": symbol,
            "price": price,
            "direction": direction,
            "chat_id": chat_key,
        }
        self._price_alerts.setdefault(chat_key, []).append(alarm)
        await update.message.reply_text(
            f"Alarm kuruldu #{alarm['id']}: {symbol} "
            f"{'>=' if direction == 'above' else '<='} {price}"
        )

    async def cmd_alarmlar(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Aktif alarmları listele."""
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        chat_key = str(update.effective_chat.id)
        alarms = self._price_alerts.get(chat_key, [])
        if not alarms:
            await update.message.reply_text("Aktif alarm yok")
            return
        lines = ["*Aktif Alarmlar*\n"]
        for a in alarms:
            arrow = ">=" if a["direction"] == "above" else "<="
            lines.append(
                f"- #{a['id']} `{a['symbol']}` {arrow} `{a['price']}`"
            )
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )

    async def cmd_alarmsil(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/alarmsil ID"""
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not context.args:
            await update.message.reply_text("Kullanim: /alarmsil 3")
            return
        try:
            aid = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Gecersiz ID")
            return
        chat_key = str(update.effective_chat.id)
        alarms = self._price_alerts.get(chat_key, [])
        before = len(alarms)
        self._price_alerts[chat_key] = [a for a in alarms if a["id"] != aid]
        removed = before - len(self._price_alerts[chat_key])
        if removed:
            await update.message.reply_text(f"Alarm #{aid} silindi")
        else:
            await update.message.reply_text(f"#{aid} bulunamadi")

    async def _check_alerts(self, prices: Dict[str, float]):
        """Live_prices geldiğinde tüm chat alarmlarını kontrol et.

        Tetiklenen alarmlar kaldırılır (tek seferlik). Her tetikleme için
        kullanıcıya bildirim gönderir.
        """
        if not self.app or not prices:
            return
        triggered_messages: List[tuple] = []
        for chat_key, alarms in list(self._price_alerts.items()):
            kept = []
            for a in alarms:
                p = prices.get(a["symbol"])
                if p is None:
                    kept.append(a)
                    continue
                is_hit = (
                    (a["direction"] == "above" and p >= a["price"])
                    or (a["direction"] == "below" and p <= a["price"])
                )
                if is_hit:
                    arrow = ">=" if a["direction"] == "above" else "<="
                    triggered_messages.append((
                        chat_key,
                        f"*Alarm* #{a['id']} tetiklendi\n"
                        f"`{a['symbol']}` {arrow} `{a['price']}` — "
                        f"guncel: `{p:.6f}`"
                    ))
                else:
                    kept.append(a)
            self._price_alerts[chat_key] = kept

        for chat_key, msg in triggered_messages:
            try:
                await self.app.bot.send_message(
                    chat_id=int(chat_key),
                    text=msg,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error(f"Alarm gonderim hatasi: {e}")

    # ============================================================
    # GUNLUK OTOMATIK RAPOR (BLOK 5A)
    # ============================================================
    async def _daily_report_job(self, context: ContextTypes.DEFAULT_TYPE):
        """JobQueue tarafından her gün tetiklenir - PnL özeti gönderir."""
        if not self.trader:
            return
        try:
            daily = db.get_daily_pnl()
            stats = db.get_trade_stats(days=1)  # Son 24h
            stats30 = db.get_trade_stats(days=30)
            unr = self.trader.portfolio.total_unrealized_pnl
            pos_count = self.trader.portfolio.open_positions_count()
            bot_state = self.trader.status.value

            sign = "+" if daily >= 0 else ""
            msg = (
                f"*Gunluk Rapor*\n\n"
                f"Bot durumu: `{bot_state.upper()}`\n"
                f"Bugunku PnL: `{sign}{daily:.4f}` USDT\n"
                f"Gerceklesmemis: `{unr:+.4f}` USDT\n"
                f"Son 24h trade: `{stats['total_trades']}`\n"
                f"Son 24h win rate: `{stats['win_rate']:.2f}%`\n"
                f"Acik pozisyon: `{pos_count}`\n"
                f"\n*30 gunluk*\n"
                f"Toplam PnL: `{stats30['total_pnl']:+.4f}`\n"
                f"Win Rate: `{stats30['win_rate']:.2f}%`\n"
                f"En iyi: `{stats30['best_trade']:+.4f}`"
            )
            await context.bot.send_message(
                chat_id=self.chat_id,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error(f"Daily report hatasi: {e}")

    def schedule_daily_report(self):
        """JobQueue ile her gün 08:00 UTC'de rapor planla.

        NOT: python-telegram-bot[job-queue] extras paketi kurulu değilse
        app.job_queue None olur. Sessizce geç.
        """
        if not self.app or not getattr(self.app, "job_queue", None):
            logger.info("JobQueue yok - daily report plani atlaniyor")
            return
        try:
            self.app.job_queue.run_daily(
                self._daily_report_job,
                time=dtime(hour=8, minute=0, tzinfo=timezone.utc),
                name="daily_report",
            )
            logger.info("Gunluk rapor 08:00 UTC'ye planlandi")
        except Exception as e:
            logger.error(f"schedule_daily_report hatasi: {e}")

    # ============================================================
    # PAPER MODE KOMUTLARI (BLOK 5B)
    # ============================================================
    async def cmd_papermode(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/papermode on|off — paper trading'i aç/kapat."""
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        arg = (context.args[0].lower() if context.args else "").strip()
        if arg not in ("on", "off"):
            await update.message.reply_text("Kullanim: /papermode on veya /papermode off")
            return
        try:
            from bot.paper_trader import get_paper_trader
            pt = get_paper_trader()
            if arg == "on":
                await pt.start()
                await update.message.reply_text(
                    f"Paper mode ON. Bakiye: {pt.paper_balance:.2f} USDT"
                )
            else:
                await pt.stop()
                await update.message.reply_text("Paper mode OFF.")
        except Exception as e:
            logger.error(f"cmd_papermode hatasi: {e}")
            await update.message.reply_text("Bir hata olustu.")

    async def cmd_paperstatus(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/paperstatus — paper bakiye ve pozisyonlar."""
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        try:
            from bot.paper_trader import get_paper_trader
            pt = get_paper_trader()
            summary = pt.get_paper_summary()
            pnl = summary["total_pnl"]
            sign = "+" if pnl >= 0 else ""
            lines = [
                "*Paper Trading*\n",
                f"Aktif: `{'EVET' if summary['enabled'] else 'HAYIR'}`",
                f"Baslangic: `{summary['initial_balance']:.2f}` USDT",
                f"Mevcut bakiye: `{summary['paper_balance']:.4f}` USDT",
                f"Equity: `{summary['equity']:.4f}` USDT",
                f"Toplam PnL: `{sign}{pnl:.4f}` ({summary['total_pnl_pct']:+.2f}%)",
                f"Acik pozisyon: `{summary['open_positions_count']}`",
                f"Toplam trade: `{summary['total_trades']}`",
            ]
            if summary["open_positions"]:
                lines.append("\n*Pozisyonlar*")
                for p in summary["open_positions"][:5]:
                    lines.append(
                        f"- `{p['symbol']}` {p['side'].upper()} "
                        f"{p['amount']:.6f} @ {p['entry_price']:.4f} "
                        f"PnL: `{p['unrealized_pnl']:+.4f}`"
                    )
            await update.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"cmd_paperstatus hatasi: {e}")
            await update.message.reply_text("Bir hata olustu.")

    async def cmd_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Inline keyboard callback handler.

        NOT: `update.callback_query.message.reply_text` doğrudan çalışır.
        Fake Update objesi yaratmak yerine action'a göre doğrudan içerik
        gönderiyoruz. Böylece `update.message` None olma riskleri giderilir.
        """
        if not self._is_authorized(update):
            return
        query = update.callback_query
        await query.answer()
        action = query.data or ""
        chat_id = query.message.chat_id if query.message else self.chat_id

        async def _reply(text: str):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error(f"Callback reply hatasi: {e}")

        try:
            if action == "status" and self.trader:
                st = self.trader.get_status()
                active = st.get("active_strategies", 0)
                total = len(self.trader.strategies)
                await _reply(
                    f"*Bot Durumu*\n\n"
                    f"Durum: `{st['status'].upper()}`\n"
                    f"Aktif strateji: `{active}/{total}`\n"
                    f"Izlenen sembol: `{len(st['symbols'])}`\n"
                    f"Toplam USDT: `{st['portfolio']['total_usdt']:.4f}`\n"
                    f"Acik pozisyon: `{st['portfolio']['open_positions_count']}`"
                )
            elif action == "balance" and self.trader:
                bal = await self.trader.exchange.get_balance()
                lines = [f"*Bakiye*\nToplam USDT: `{bal.get('usdt_value', 0):.4f}`"]
                for cur, amount in bal.get("total", {}).items():
                    free = bal.get("free", {}).get(cur, 0)
                    lines.append(f"  {cur}: `{amount:.8f}` (serbest: `{free:.8f}`)")
                await _reply("\n".join(lines))
            elif action == "start" and self.trader:
                await self.trader.start()
                await _reply("Bot baslatildi")
            elif action == "stop" and self.trader:
                await self.trader.stop()
                await _reply("Bot durduruldu")
            elif action == "pause" and self.trader:
                if self.trader.status.value == "paused":
                    await self.trader.resume()
                    await _reply("Bot devam ediyor")
                else:
                    await self.trader.pause()
                    await _reply("Bot duraklatildi")
            elif action == "pnl" and self.trader:
                daily = db.get_daily_pnl()
                stats = db.get_trade_stats(days=30)
                unr = self.trader.portfolio.total_unrealized_pnl
                await _reply(
                    f"*PnL*\n"
                    f"Bugunku: `{daily:+.4f}` USDT\n"
                    f"Gerceklesmemis: `{unr:+.4f}` USDT\n"
                    f"30g Toplam: `{stats['total_pnl']:+.4f}`\n"
                    f"Win Rate: `{stats['win_rate']:.2f}%`"
                )
            elif action == "trades":
                trades = db.get_trades(limit=5)
                if not trades:
                    await _reply("Trade yok")
                else:
                    lines = ["*Son 5 Trade*"]
                    for t in trades:
                        sign = "+" if (t.get("pnl") or 0) >= 0 else ""
                        lines.append(
                            f"- `{t['symbol']}` {t['side'].upper()} "
                            f"{t['amount']} @ {t['price']:.4f} | "
                            f"PnL `{sign}{t.get('pnl') or 0:.4f}`"
                        )
                    await _reply("\n".join(lines))
        except Exception as e:
            logger.error(f"cmd_callback [{action}] hatasi: {e}")
            await _reply("Bir hata olustu, lutfen tekrar deneyin.")

    # ============================================================
    # BILDIRIMLER
    # ============================================================
    async def notify(self, data: Dict[str, Any]):
        """Trader'dan gelen event'leri telegram'a gönder"""
        if not self.enabled or not self.app:
            return
        event = data.get("event", "")

        # Fiyat güncellemeleri -> alarm kontrolü (her tick'te çalışır)
        if event == "prices_updated":
            prices = data.get("prices") or {}
            if prices:
                try:
                    await self._check_alerts(prices)
                except Exception as e:
                    logger.debug(f"alarm check hatasi: {e}")
            return  # prices_updated için başka msg yok

        try:
            msg = None
            if event == "trade_executed":
                t = data.get("trade", {})
                msg = (
                    f"*Trade*\n\n"
                    f"{t.get('side', '').upper()} `{t.get('symbol')}`\n"
                    f"Miktar: `{t.get('amount')}`\n"
                    f"Fiyat: `{t.get('price')}`\n"
                    f"Strateji: `{t.get('strategy')}`\n"
                    f"Sebep: {t.get('reason', '')}"
                )
            elif event == "position_closed":
                t = data.get("trade", {})
                pnl = t.get("pnl", 0)
                sign = "+" if pnl >= 0 else ""
                msg = (
                    f"*Pozisyon Kapandi*\n\n"
                    f"`{t.get('symbol')}` {t.get('side', '').upper()}\n"
                    f"PnL: `{sign}{pnl:.4f}` USDT ({t.get('pnl_pct', 0):.2f}%)"
                )
            elif event == "bot_started":
                msg = "Bot *baslatildi*"
            elif event == "bot_stopped":
                msg = "Bot *durduruldu*"
            elif event == "bot_paused":
                msg = "Bot *duraklatildi*"

            if msg:
                await self.app.bot.send_message(
                    chat_id=self.chat_id,
                    text=msg,
                    parse_mode=ParseMode.MARKDOWN,
                )
        except Exception as e:
            logger.error(f"Telegram notify hatasi: {e}")

    # ============================================================
    # LIFECYCLE
    # ============================================================
    async def start(self):
        """Telegram botu başlat"""
        if not self.enabled:
            logger.info("Telegram devre disi (token veya chat_id yok)")
            return

        self.app = Application.builder().token(self.token).build()

        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("balance", self.cmd_balance))
        self.app.add_handler(CommandHandler("positions", self.cmd_positions))
        self.app.add_handler(CommandHandler("pnl", self.cmd_pnl))
        self.app.add_handler(CommandHandler("trades", self.cmd_trades))
        self.app.add_handler(CommandHandler("startbot", self.cmd_startbot))
        self.app.add_handler(CommandHandler("stopbot", self.cmd_stopbot))
        self.app.add_handler(CommandHandler("pausebot", self.cmd_pausebot))
        self.app.add_handler(CommandHandler("setpair", self.cmd_setpair))
        # Fiyat alarmları (BLOK 5A)
        self.app.add_handler(CommandHandler("alarm", self.cmd_alarm))
        self.app.add_handler(CommandHandler("alarmlar", self.cmd_alarmlar))
        self.app.add_handler(CommandHandler("alarmsil", self.cmd_alarmsil))
        # Paper mode (BLOK 5B)
        self.app.add_handler(CommandHandler("papermode", self.cmd_papermode))
        self.app.add_handler(CommandHandler("paperstatus", self.cmd_paperstatus))
        self.app.add_handler(CallbackQueryHandler(self.cmd_callback))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)

        # Günlük raporu zamanla (JobQueue varsa)
        try:
            self.schedule_daily_report()
        except Exception as e:
            logger.debug(f"daily report scheduling: {e}")

        logger.info("Telegram bot baslatildi")

        # Başlangıç bildirimi
        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=f"*AgentTrader v{Config.VERSION}* baslatildi",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.warning(f"Telegram hos geldiniz mesaji gonderilemedi: {e}")

    async def stop(self):
        """Telegram botu durdur"""
        if not self.app:
            return
        try:
            # Updater - None veya running değilse atla
            if getattr(self.app, "updater", None) is not None:
                try:
                    if self.app.updater.running:
                        await self.app.updater.stop()
                except Exception as e:
                    logger.debug(f"Updater stop: {e}")
            try:
                if self.app.running:
                    await self.app.stop()
            except Exception as e:
                logger.debug(f"App stop: {e}")
            try:
                await self.app.shutdown()
            except Exception as e:
                logger.debug(f"App shutdown: {e}")
            logger.info("Telegram bot durduruldu")
        except Exception as e:
            logger.error(f"Telegram stop hatasi: {e}")
