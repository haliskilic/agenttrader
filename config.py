"""
AgentTrader - Merkezi Konfigürasyon Modülü
Tüm ayarlar environment variables + default değerlerden okunur.
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# .env dosyasını yükle
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    """Uygulama konfigürasyonu"""

    # ============================================================
    # GENEL
    # ============================================================
    APP_NAME = "AgentTrader"
    VERSION = "1.0.0"
    BASE_DIR = BASE_DIR
    LOG_DIR = BASE_DIR / "logs"
    DATA_DIR = BASE_DIR / "data"

    # ============================================================
    # OKX EXCHANGE
    # ============================================================
    OKX_API_KEY = os.getenv("OKX_API_KEY", "")
    OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY", "")
    OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
    OKX_SANDBOX = os.getenv("OKX_SANDBOX", "false").lower() == "true"

    # CCXT OKX konfigürasyonu
    CCXT_CONFIG = {
        "apiKey": OKX_API_KEY,
        "secret": OKX_SECRET_KEY,
        "password": OKX_PASSPHRASE,
        "enableRateLimit": True,
        "rateLimit": 100,  # ms
        "timeout": 30000,
        "options": {
            "defaultType": "spot",  # spot/swap/futures
            "adjustForTimeDifference": True,
        },
    }

    # ============================================================
    # TRADING
    # ============================================================
    # İzlenecek semboller
    DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    # Default strateji
    DEFAULT_STRATEGY = "rsi_bb"

    # Base currency (bakiye için)
    QUOTE_CURRENCY = "USDT"

    # ============================================================
    # RISK MANAGEMENT
    # ============================================================
    MAX_POSITION_SIZE_PCT = 0.05      # Toplam portföyün %5'i
    STOP_LOSS_PCT = 0.02              # %2 stop loss
    TAKE_PROFIT_PCT = 0.04            # %4 take profit
    MAX_DAILY_LOSS_PCT = 0.10         # Günlük max %10 kayıp
    MAX_OPEN_POSITIONS = 5            # Max 5 açık pozisyon
    MAX_DRAWDOWN_PCT = 0.20           # Max %20 drawdown

    # Minimum işlem miktarı (USDT)
    MIN_ORDER_VALUE = 10.0

    # ============================================================
    # STRATEJİLER
    # ============================================================
    STRATEGY_CONFIGS = {
        "rsi_bb": {
            "enabled": True,
            "timeframe": "15m",
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "bb_period": 20,
            "bb_std": 2.0,
            "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        },
        "ma_crossover": {
            "enabled": False,
            "timeframe": "1h",
            "ema_fast": 9,
            "ema_slow": 21,
            "symbols": ["BTC/USDT", "ETH/USDT"],
        },
        "grid_trading": {
            "enabled": False,
            "timeframe": "5m",
            "lower_price_pct": 0.95,   # Mevcut fiyatın %95'i
            "upper_price_pct": 1.05,   # Mevcut fiyatın %105'i
            "grid_count": 10,
            "amount_per_grid": 10.0,   # USDT
            "symbols": ["BTC/USDT"],
        },
        "macd_signal": {
            "enabled": False,
            "timeframe": "1h",
            "symbols": ["BTC/USDT", "ETH/USDT"],
            "fast": 12,
            "slow": 26,
            "signal_period": 9,
            "ema_trend_period": 200,
            "stop_loss_pct": 0.025,
            "take_profit_pct": 0.05,
        },
        "stoch_rsi": {
            "enabled": False,
            "timeframe": "30m",
            "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            "rsi_period": 14,
            "stoch_period": 14,
            "k_smooth": 3,
            "d_smooth": 3,
            "volume_mult": 1.2,
            "stop_loss_pct": 0.018,
            "take_profit_pct": 0.036,
        },
    }

    # Trading döngü aralıkları (saniye)
    STRATEGY_LOOP_INTERVAL = 30       # Her strateji 30 saniyede bir çalışır
    PRICE_UPDATE_INTERVAL = 1         # Fiyat güncelleme aralığı

    # ============================================================
    # PAPER TRADING
    # ============================================================
    # Paper trading modu: gerçek emir vermeden canlı fiyatlarla simüle eder.
    # True iken Trader yerine PaperTrader devreye alınır (runtime'da toggle
    # edilebilir - web/api paper endpoint'leri). Env default'u kapalı tutar ki
    # yanlışlıkla paper mode'da canlı trading yaptığını zanneden kullanıcı olmasın.
    PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "false").lower() in ("true", "1", "yes")
    PAPER_BALANCE: float = float(os.getenv("PAPER_BALANCE", "10000.0"))

    # ============================================================
    # DATABASE
    # ============================================================
    DB_PATH = BASE_DIR / "data" / "agenttrader.db"
    # Windows backslash yerine forward slash kullan (SQLAlchemy URL uyumluluğu)
    DB_URL = f"sqlite:///{DB_PATH.as_posix()}"

    # ============================================================
    # WEB DASHBOARD
    # ============================================================
    # Bind adresi - default 127.0.0.1 (yalnızca lokal). Uzaktan erişim için .env'de 0.0.0.0 yapın.
    WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
    WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
    # GÜVENLİK: Default değer uyarı için; production'da MUTLAKA .env'den sağlam random değer verin.
    WEB_SECRET_KEY = os.getenv("WEB_SECRET_KEY", "")
    # Auth aktif olsun mu? Açık env var ile kontrol edilir (default: true).
    # Secret key set etmeyi unutan operatörün auth'u sessizce devre dışı
    # bırakmasını engeller: WEB_AUTH_ENABLED=true iken secret boşsa otomatik
    # bir key üretilir (yalnızca uyarı amaçlı, production'da asla bu path'e düşmeyin).
    WEB_AUTH_ENABLED: bool = os.getenv("WEB_AUTH_ENABLED", "true").lower() not in ("false", "0", "no")

    # CORS - default olarak same-origin; .env'den WEB_CORS_ORIGINS ile override edilebilir.
    _cors_env = os.getenv("WEB_CORS_ORIGINS", "")
    CORS_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()] or [
        f"http://localhost:{WEB_PORT}",
        f"http://127.0.0.1:{WEB_PORT}",
    ]

    # ============================================================
    # TELEGRAM BOT
    # ============================================================
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    TELEGRAM_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

    # ============================================================
    # LOGGING
    # ============================================================
    LOG_LEVEL = logging.INFO
    LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
    LOG_FILE = LOG_DIR / "agenttrader.log"

    @classmethod
    def ensure_dirs(cls):
        """Gerekli dizinleri oluştur"""
        cls.LOG_DIR.mkdir(parents=True, exist_ok=True)
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate(cls):
        """Kritik konfigürasyonları doğrula"""
        errors = []
        if not cls.OKX_API_KEY:
            errors.append("OKX_API_KEY tanimlanmamis")
        if not cls.OKX_SECRET_KEY:
            errors.append("OKX_SECRET_KEY tanimlanmamis")
        if not cls.OKX_PASSPHRASE:
            errors.append("OKX_PASSPHRASE tanimlanmamis")
        return errors


def setup_logging():
    """Logging yapılandırması - konsol + rotating dosya.

    Log rotation: agenttrader.log 10MB'a ulaştığında .1, .2, ... olarak
    arşivlenir ve en fazla 5 backup tutulur. Böylece uzun süre çalışan bot
    disk'i tek bir büyük log dosyası ile doldurmaz.
    """
    from logging.handlers import RotatingFileHandler

    Config.ensure_dirs()

    logger = logging.getLogger()
    logger.setLevel(Config.LOG_LEVEL)

    # Mevcut handlerları temizle
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    formatter = logging.Formatter(Config.LOG_FORMAT)

    # Konsol handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(Config.LOG_LEVEL)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating dosya handler: 10MB per file, 5 backup (toplam ~50MB)
    file_handler = RotatingFileHandler(
        Config.LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(Config.LOG_LEVEL)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # CCXT'nin gereksiz loglarını susturma
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # Telegram token URL loglanmasın

    return logger


# Konfigürasyonu dışa aktar
config = Config()
