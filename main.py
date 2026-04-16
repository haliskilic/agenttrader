"""
AgentTrader - Ana Başlangıç Noktası

Tüm bileşenleri (Web sunucu, Telegram bot, Trading engine) asyncio ile
paralel başlatır ve graceful shutdown uygular.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

import uvicorn


def _utcnow() -> datetime:
    """UTC timestamp - _utcnow() deprecated in Python 3.12+."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

from config import Config, setup_logging
from database import db

# Bot
from bot.trader import get_trader

# Web
from web.app import create_app
from web.routes.ws import notify_event as ws_notify

# Telegram
from telegram_bot.bot import TelegramBot


LOGO = r"""
    _                    _  _____             _
   / \   __ _  ___ _ __ | ||_   _| __ __ _  __| | ___ _ __
  / _ \ / _` |/ _ \ '_ \| __|| || '__/ _` |/ _` |/ _ \ '__|
 / ___ \ (_| |  __/ | | | |_ | || | | (_| | (_| |  __/ |
/_/   \_\__, |\___|_| |_|\__||_||_|  \__,_|\__,_|\___|_|
        |___/                                           v{version}
"""


logger = logging.getLogger(__name__)


class Application:
    """Ana uygulama orchestrator"""

    def __init__(self):
        self.trader = None
        self.telegram = None
        self.web_server = None
        self.web_server_task = None
        self._shutdown_event = asyncio.Event()

    async def start(self):
        """Tüm bileşenleri başlat"""
        print(LOGO.format(version=Config.VERSION))
        print(f"  OKX Crypto Trading Bot  |  Sandbox: {Config.OKX_SANDBOX}")
        print(f"  Web Dashboard: http://localhost:{Config.WEB_PORT}")
        print(f"  Telegram: {'Aktif' if Config.TELEGRAM_ENABLED else 'Devre Disi'}")
        print("-" * 70)

        # Konfigürasyon kontrolü
        errors = Config.validate()
        if errors:
            for e in errors:
                logger.error(f"Config hatasi: {e}")
            logger.error(".env dosyanizi kontrol edin")
            sys.exit(1)

        # Trader
        self.trader = get_trader()
        db.set_state("bot_started_at", _utcnow().isoformat())

        # Telegram
        self.telegram = TelegramBot(trader=self.trader)

        # Callback'leri bağla: trader eventleri -> WS + Telegram
        async def combined_notify(data):
            try:
                await ws_notify(data)
            except Exception as e:
                logger.error(f"WS notify hatasi: {e}")
            try:
                if self.telegram and self.telegram.enabled:
                    await self.telegram.notify(data)
            except Exception as e:
                logger.error(f"Telegram notify hatasi: {e}")

        self.trader.add_notification_callback(combined_notify)

        # Telegram start (başarısız olursa devam et - web/trading kritik değil)
        try:
            await self.telegram.start()
        except Exception as e:
            logger.error(f"Telegram baslatilamadi: {e}")
            # self.telegram.enabled'i False'a çekmeyelim; notify fonksiyonu
            # kendi içinde enabled + app kontrolü yapıyor.

        # Web sunucusu
        app = create_app()
        config = uvicorn.Config(
            app,
            host=Config.WEB_HOST,
            port=Config.WEB_PORT,
            log_level="info",
            lifespan="on",
        )
        self.web_server = uvicorn.Server(config)
        self.web_server_task = asyncio.create_task(
            self.web_server.serve(), name="web_server"
        )
        logger.info(f"Web sunucusu http://localhost:{Config.WEB_PORT}")

        # Signal scanner — bot kapalı olsa bile sinyal tarama çalışsın
        asyncio.create_task(self._start_scanner_delayed(), name="scanner_init")

        # Signal handlers
        self._setup_signal_handlers()

        logger.info("AgentTrader basariyla baslatildi")
        logger.info("Bot'u dashboard veya /startbot komutu ile baslatabilirsiniz")

        # Shutdown bekleme
        await self._shutdown_event.wait()

    async def _start_scanner_delayed(self):
        """Web sunucusu ayağa kalktıktan sonra scanner'ı başlat"""
        await asyncio.sleep(5)
        try:
            await self.trader.exchange.connect()
            await self.trader.start_scanner()
        except Exception as e:
            logger.error(f"Scanner baslatilamadi: {e}")

    def _setup_signal_handlers(self):
        """Ctrl+C / SIGTERM için graceful shutdown"""
        loop = asyncio.get_running_loop()

        def _handler():
            logger.info("Kapatma sinyali alindi")
            self._shutdown_event.set()

        # Windows uyumluluğu
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _handler)
        except (NotImplementedError, AttributeError):
            # Windows add_signal_handler desteklemiyor
            signal.signal(signal.SIGINT, lambda *_: _handler())
            try:
                signal.signal(signal.SIGTERM, lambda *_: _handler())
            except Exception:
                pass

    async def shutdown(self):
        """Tüm bileşenleri düzgünce kapat"""
        logger.info("Graceful shutdown basliyor...")

        # Trader
        try:
            if self.trader:
                await self.trader.stop()
        except Exception as e:
            logger.error(f"Trader stop hatasi: {e}")

        # Telegram
        try:
            if self.telegram:
                await self.telegram.stop()
        except Exception as e:
            logger.error(f"Telegram stop hatasi: {e}")

        # Web
        try:
            if self.web_server:
                self.web_server.should_exit = True
            if self.web_server_task:
                try:
                    await asyncio.wait_for(self.web_server_task, timeout=5)
                except asyncio.TimeoutError:
                    self.web_server_task.cancel()
        except Exception as e:
            logger.error(f"Web stop hatasi: {e}")

        logger.info("AgentTrader kapatildi")


async def main():
    setup_logging()
    app = Application()
    try:
        await app.start()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt")
    except Exception as e:
        logger.exception(f"Beklenmedik hata: {e}")
    finally:
        await app.shutdown()


if __name__ == "__main__":
    try:
        if sys.platform == "win32" and sys.version_info < (3, 16):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCikis...")
