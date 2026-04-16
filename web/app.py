"""
FastAPI Uygulaması
- REST API
- WebSocket
- Static files
- Basit API key auth
- slowapi ile rate limiting (BLOK 8)
- Detaylı /health endpoint (BLOK 9)
"""

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from config import Config
from .routes import api, ws

logger = logging.getLogger(__name__)

# Process başlangıç zamanı — /health uptime için
_process_start = time.time()


# ============================================================
# RATE LIMITER
# ============================================================
# slowapi opsiyonel dependency olabilir - import hatasında limiter=None
# olur ve rate limiting pas geçilir. Production'da kurulu olması beklenir.
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address
    limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
    _HAS_SLOWAPI = True
except ImportError:
    logger.warning("slowapi kurulu degil - rate limiting devre disi")
    limiter = None
    _HAS_SLOWAPI = False


def create_app() -> FastAPI:
    """FastAPI uygulamasını oluştur"""

    app = FastAPI(
        title=Config.APP_NAME,
        version=Config.VERSION,
        description="OKX Crypto Trading Bot Dashboard",
    )

    # Rate limiter binding
    if _HAS_SLOWAPI and limiter is not None:
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        logger.info("Rate limiter aktif (default 60/min)")

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=Config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Route'lar
    app.include_router(api.router, prefix="/api", tags=["api"])
    app.include_router(ws.router, tags=["ws"])

    # Static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount(
            "/static", StaticFiles(directory=str(static_dir)), name="static"
        )

    @app.get("/")
    async def root():
        """Dashboard ana sayfa"""
        index = static_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"message": f"{Config.APP_NAME} {Config.VERSION}"}

    @app.get("/health")
    async def health():
        """Detaylı health check.

        Kontrol edilenler
        ------------------
        - bot_status: trader durumu
        - exchange_connected: ccxt client bağlı mı
        - db_accessible: DB'ye yazma/okuma yapılabiliyor mu
        - telegram_active: telegram app başlatılmış mı
        - uptime_seconds: process başlayalı kaç saniye
        - version
        """
        result = {
            "status": "ok",
            "version": Config.VERSION,
            "uptime_seconds": round(time.time() - _process_start, 2),
            "bot_status": "unknown",
            "exchange_connected": False,
            "db_accessible": False,
            "telegram_active": False,
            "paper_trading_active": False,
        }

        # Bot status
        try:
            from bot.trader import get_trader
            trader = get_trader()
            result["bot_status"] = trader.status.value
            result["exchange_connected"] = trader.exchange._client is not None
        except Exception as e:
            logger.debug(f"health: trader sorgu hatasi: {e}")
            result["status"] = "degraded"

        # DB accessibility: basit state read/write testi
        try:
            from database import db as _db
            _db.set_state("_health_check", str(int(time.time())))
            result["db_accessible"] = _db.get_state("_health_check") is not None
        except Exception as e:
            logger.debug(f"health: db sorgu hatasi: {e}")
            result["status"] = "degraded"

        # Telegram
        try:
            # Telegram singleton main.py'da atanıyor; burada config flag'ine bakarız.
            result["telegram_active"] = bool(
                Config.TELEGRAM_ENABLED and Config.TELEGRAM_TOKEN
            )
        except Exception:
            pass

        # Paper trading
        try:
            from bot.paper_trader import get_paper_trader
            result["paper_trading_active"] = get_paper_trader().enabled
        except Exception:
            pass

        return result

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        # HTTPException'lar FastAPI'nin kendi handler'ı tarafından işlenmeli
        # (401, 404 vs. doğru status kod ile dönsün).
        if isinstance(exc, HTTPException):
            return JSONResponse(
                status_code=exc.status_code,
                content={"success": False, "error": exc.detail},
            )
        logger.exception(f"Unhandled exception: {exc}")
        # Güvenlik: iç hata mesajını kullanıcıya ifşa etme (stack trace sızıntısı)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Internal server error"},
        )

    return app
