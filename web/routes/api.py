"""
REST API Endpoints
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from config import Config
from database import db

logger = logging.getLogger(__name__)

# slowapi opsiyonel — yoksa no-op decorator kullanıyoruz.
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    _limiter = Limiter(key_func=get_remote_address)

    def rate_limit(spec: str) -> Callable:
        return _limiter.limit(spec)
except ImportError:  # pragma: no cover - slowapi yoksa
    def rate_limit(spec: str) -> Callable:
        def _noop(fn: Callable) -> Callable:
            return fn
        return _noop

# In-memory alarm store. Restart'ta sıfırlanır; kritik alarmlar Telegram
# tarafındaki cmd_alarm ile ayrıca tutuluyor. Burası dashboard-odaklı.
_price_alarms: List[Dict[str, Any]] = []
_alarm_id_counter: int = 0


# ============================================================
# AUTH
# ============================================================
def check_auth(x_api_key: Optional[str] = Header(None)):
    """API key kontrolü.

    WEB_SECRET_KEY tanımlı ise tüm istekler için X-API-Key header zorunlu.
    Key boş bırakıldıysa auth devre dışı (yalnızca local dev önerilir).
    """
    if not Config.WEB_AUTH_ENABLED:
        # Auth kapalı - yalnızca lokal dev için
        return True
    if x_api_key != Config.WEB_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Gecersiz veya eksik API key")
    return True


# Router: tüm endpoint'ler auth dependency'sinden geçer. Auth devre dışıysa no-op.
router = APIRouter(dependencies=[Depends(check_auth)])


def _trader():
    """Lazy trader access (circular import'u önlemek için)"""
    from bot.trader import get_trader
    return get_trader()


# ============================================================
# MODELLER
# ============================================================
class ManualOrderRequest(BaseModel):
    symbol: str
    side: str = Field(..., pattern="^(buy|sell)$")
    amount: float = Field(..., gt=0)
    order_type: str = Field("market", pattern="^(market|limit)$")
    price: Optional[float] = None


class SymbolRequest(BaseModel):
    symbol: str
    action: str = Field("add", pattern="^(add|remove)$")


class StrategyToggleRequest(BaseModel):
    enabled: Optional[bool] = None


class BacktestRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "15m"
    strategy: str = "rsi_bb"
    initial_balance: float = Field(1000.0, gt=0)
    stop_loss_pct: float = Field(0.02, gt=0, lt=1)
    take_profit_pct: float = Field(0.04, gt=0, lt=1)
    position_size_pct: float = Field(0.05, gt=0, lt=1)
    limit: int = Field(500, ge=100, le=2000)
    demo_mode: bool = False

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        # Whitelist: sadece known strategy'ler geçsin. Aksi halde attacker
        # rastgele string göndererek hata mesajlarından bilgi sızdırabilir.
        allowed = {"rsi_bb", "ma_crossover", "grid_trading", "macd_signal", "stoch_rsi"}
        if v not in allowed:
            raise ValueError(f"Gecersiz strateji. Izin verilenler: {sorted(allowed)}")
        return v


class BacktestCompareRequest(BaseModel):
    """Birden fazla stratejiyi ayni OHLCV verisi uzerinde karsilastirmak icin."""
    symbol: str = "BTC/USDT"
    timeframe: str = "15m"
    strategies: List[str] = Field(default_factory=lambda: ["rsi_bb"])
    balance: float = Field(1000.0, gt=0)
    limit: int = Field(500, ge=100, le=2000)
    stop_loss_pct: float = Field(0.02, gt=0, lt=1)
    take_profit_pct: float = Field(0.04, gt=0, lt=1)
    position_size_pct: float = Field(0.05, gt=0, lt=1)

    @field_validator("strategies")
    @classmethod
    def validate_strategies(cls, v: List[str]) -> List[str]:
        allowed = {"rsi_bb", "ma_crossover", "grid_trading", "macd_signal", "stoch_rsi"}
        for s in v:
            if s not in allowed:
                raise ValueError(f"Gecersiz strateji: {s}")
        if not v:
            raise ValueError("En az bir strateji gerekli")
        if len(v) > 5:
            raise ValueError("En fazla 5 strateji karsilastirilabilir")
        return v


# ============================================================
# BOT KONTROL
# ============================================================
@router.get("/status")
async def get_status():
    """Bot durumu"""
    try:
        return {"success": True, "data": _trader().get_status()}
    except Exception as e:
        logger.error(f"/status hatasi: {e}")
        return {"success": False, "error": str(e)}


@router.post("/bot/start")
@rate_limit("5/minute")
async def start_bot(request: Request):
    """Bot'u başlat.

    Rate limit: 5/min — kullanıcının start/stop spam yapmasını engeller.
    """
    try:
        await _trader().start()
        return {"success": True, "message": "Bot baslatildi"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/bot/stop")
async def stop_bot():
    """Bot'u durdur"""
    try:
        await _trader().stop()
        return {"success": True, "message": "Bot durduruldu"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/bot/pause")
async def pause_bot():
    """Bot'u duraklat"""
    try:
        await _trader().pause()
        return {"success": True, "message": "Bot duraklatildi"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/bot/resume")
async def resume_bot():
    """Bot'u devam ettir"""
    try:
        await _trader().resume()
        return {"success": True, "message": "Bot devam ediyor"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# PORTFÖY
# ============================================================
@router.get("/balance")
async def get_balance():
    """Bakiye"""
    trader = _trader()
    try:
        bal = await trader.exchange.get_balance()
        trader.portfolio.update_balance(bal)
        return {"success": True, "data": bal}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/positions")
async def get_positions():
    """Açık pozisyonlar"""
    try:
        return {
            "success": True,
            "data": _trader().portfolio.open_positions_list(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/trades")
async def get_trades(
    symbol: Optional[str] = None, limit: int = Query(50, ge=1, le=500)
):
    """Trade geçmişi"""
    try:
        trades = db.get_trades(symbol=symbol, limit=limit)
        return {"success": True, "data": trades}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/pnl")
async def get_pnl(days: int = Query(30, ge=1, le=365)):
    """PnL ve istatistikler"""
    try:
        stats = db.get_trade_stats(days=days)
        metrics = _trader().portfolio.compute_metrics(days=days)
        return {
            "success": True,
            "data": {
                "period_days": days,
                "stats": stats,
                "metrics": metrics,
                "daily_pnl": db.get_daily_pnl(),
                "unrealized": _trader().portfolio.total_unrealized_pnl,
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# STRATEJİLER
# ============================================================
@router.get("/strategies")
async def list_strategies():
    """Tüm stratejiler"""
    try:
        return {"success": True, "data": _trader().get_strategies_info()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/strategies/{name}/toggle")
async def toggle_strategy(name: str, req: StrategyToggleRequest = None):
    """Strateji aç/kapa"""
    try:
        enabled = req.enabled if req else None
        result = _trader().toggle_strategy(name, enabled)
        return {"success": True, "data": {"name": name, "enabled": result}}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# EMİRLER
# ============================================================
@router.post("/order/manual")
@rate_limit("10/minute")
async def place_manual_order(request: Request, req: ManualOrderRequest):
    """Manuel emir (rate limit: 10/min - hızlı spam emirleri engeller)"""
    try:
        result = await _trader().manual_order(
            symbol=req.symbol,
            side=req.side,
            amount=req.amount,
            order_type=req.order_type,
            price=req.price,
        )
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/orders/open")
async def get_open_orders(symbol: Optional[str] = None):
    """Açık emirler"""
    try:
        orders = await _trader().exchange.get_open_orders(symbol)
        return {"success": True, "data": orders}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str, symbol: str):
    """Emri iptal et"""
    try:
        result = await _trader().exchange.cancel_order(order_id, symbol)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# SEMBOLLER
# ============================================================
@router.get("/symbols")
async def get_symbols():
    """İzlenen semboller"""
    return {"success": True, "data": _trader().symbols}


@router.post("/symbols")
async def update_symbols(req: SymbolRequest):
    """Sembol ekle/kaldır"""
    try:
        if req.action == "add":
            _trader().add_symbol(req.symbol)
        else:
            _trader().remove_symbol(req.symbol)
        return {"success": True, "data": _trader().symbols}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# OHLCV / GRAFIK VERİSİ
# ============================================================
@router.get("/ohlcv/{symbol_base}/{symbol_quote}/{timeframe}")
async def get_ohlcv(
    symbol_base: str,
    symbol_quote: str,
    timeframe: str,
    limit: int = Query(200, ge=10, le=500),
):
    """OHLCV verisi (grafik için) - URL'de / olamadığından segment ayrılır"""
    symbol = f"{symbol_base.upper()}/{symbol_quote.upper()}"
    try:
        data = await _trader().exchange.get_ohlcv(symbol, timeframe, limit)
        # Lightweight Charts formatına çevir
        candles = []
        for c in data:
            candles.append(
                {
                    "time": int(c[0] / 1000),
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5],
                }
            )
        return {"success": True, "data": candles}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# LOGS
# ============================================================
@router.get("/logs")
async def get_logs(limit: int = Query(100, ge=1, le=1000)):
    """Son log kayıtları"""
    try:
        return {"success": True, "data": db.get_logs(limit=limit)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# RISK
# ============================================================
@router.get("/risk")
async def get_risk():
    """Risk durumu"""
    try:
        trader = _trader()
        return {
            "success": True,
            "data": trader.risk_manager.get_risk_summary(trader.portfolio.total_usdt),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}



# ============================================================
# SİNYALLER
# ============================================================
@router.get("/signals")
async def get_signals(
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Strateji sinyal geçmişi (trade olmasa bile)"""
    try:
        signals = db.get_signals(symbol=symbol, strategy=strategy, limit=limit)
        return {"success": True, "data": signals}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# BACKTESTING
# ============================================================
@router.post("/backtest")
@rate_limit("3/minute")
async def run_backtest(request: Request, req: BacktestRequest):
    """Stratejiyi geçmiş veri üzerinde simüle et (rate limit: 3/min)"""
    try:
        from bot.backtester import Backtester
        trader = _trader()
        bt = Backtester(trader.exchange, trader.strategies)
        result = await bt.run(
            symbol=req.symbol,
            timeframe=req.timeframe,
            strategy_name=req.strategy,
            initial_balance=req.initial_balance,
            limit=req.limit,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct,
            position_size_pct=req.position_size_pct,
            demo_mode=req.demo_mode,
        )
        return {"success": True, "data": result.to_dict()}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Backtest hatasi: {e}")
        return {"success": False, "error": str(e)}


@router.post("/backtest/compare")
@rate_limit("2/minute")
async def backtest_compare(request: Request, req: BacktestCompareRequest):
    """Birden fazla stratejiyi ayni OHLCV verisi uzerinde karsilastir.

    OHLCV bir kez cekilir, tum stratejiler ayni veri uzerinde calistirilir —
    boylece sonuclar birebir karsilastirilabilir.
    """
    try:
        from bot.backtester import Backtester
        trader = _trader()
        exchange = trader.exchange

        # OHLCV'yi bir kez cek
        ohlcv_data = await exchange.get_ohlcv(req.symbol, req.timeframe, limit=req.limit)
        if not ohlcv_data or len(ohlcv_data) < 50:
            raise HTTPException(
                status_code=400,
                detail=f"Yetersiz veri: {len(ohlcv_data) if ohlcv_data else 0} mum",
            )

        backtester = Backtester(exchange=exchange, strategies=trader.strategies)
        results: Dict[str, Any] = {}

        for strategy_name in req.strategies:
            try:
                result = await backtester.run(
                    symbol=req.symbol,
                    timeframe=req.timeframe,
                    strategy_name=strategy_name,
                    initial_balance=req.balance,
                    limit=req.limit,
                    stop_loss_pct=req.stop_loss_pct,
                    take_profit_pct=req.take_profit_pct,
                    position_size_pct=req.position_size_pct,
                    ohlcv_data=ohlcv_data,
                )
                results[strategy_name] = result.to_dict()
            except Exception as e:
                logger.warning(f"Compare {strategy_name} hatasi: {e}")
                results[strategy_name] = {"error": str(e)}

        return {
            "success": True,
            "results": results,
            "symbol": req.symbol,
            "timeframe": req.timeframe,
            "candle_count": len(ohlcv_data),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backtest compare hatasi: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# MARKETS
# ============================================================
@router.get("/markets")
async def get_markets():
    """OKX aktif USDT spot paritelerini dondur."""
    try:
        exchange = _trader().exchange
        symbols = await exchange.get_spot_markets()
        return {"success": True, "symbols": symbols, "count": len(symbols)}
    except Exception as e:
        logger.error(f"Markets hatasi: {e}")
        return {"success": False, "symbols": [], "count": 0, "error": str(e)}


# ============================================================
# RUNTIME RISK CONFIG (BLOK 4)
# ============================================================
class RiskConfigRequest(BaseModel):
    """Runtime'da risk parametrelerini güncellemek için payload.

    Tüm alanlar opsiyonel — yalnızca gönderilen alanlar override edilir.
    Validasyon: üst/alt sınırlar makul aralıklara kısıtlı (örn. SL %50'den
    büyük olamaz — değilse validation hatası döner).
    """
    stop_loss_pct: Optional[float] = Field(None, gt=0, lt=0.5)
    take_profit_pct: Optional[float] = Field(None, gt=0, lt=1.0)
    max_position_size_pct: Optional[float] = Field(None, gt=0, lt=1.0)
    max_daily_loss_pct: Optional[float] = Field(None, gt=0, lt=1.0)
    max_open_positions: Optional[int] = Field(None, ge=1, le=20)


@router.get("/config/risk")
async def get_risk_config():
    """Mevcut risk parametrelerini döndür."""
    try:
        trader = _trader()
        return {
            "success": True,
            "data": trader.risk_manager.get_risk_summary(trader.portfolio.total_usdt),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/config/risk")
async def update_risk_config(req: RiskConfigRequest):
    """Runtime'da risk parametrelerini güncelle.

    Dikkat
    ------
    Bu değişiklikler persist edilmez; bot restart'ta Config.* default'larına
    döner. Kalıcılık için .env üzerinden set etmek gerekir.
    """
    try:
        trader = _trader()
        rm = trader.risk_manager
        updated = {}
        if req.stop_loss_pct is not None:
            rm.stop_loss_pct = req.stop_loss_pct
            updated["stop_loss_pct"] = req.stop_loss_pct
        if req.take_profit_pct is not None:
            rm.take_profit_pct = req.take_profit_pct
            updated["take_profit_pct"] = req.take_profit_pct
        if req.max_position_size_pct is not None:
            rm.max_position_pct = req.max_position_size_pct
            updated["max_position_size_pct"] = req.max_position_size_pct
        if req.max_daily_loss_pct is not None:
            rm.max_daily_loss_pct = req.max_daily_loss_pct
            updated["max_daily_loss_pct"] = req.max_daily_loss_pct
        if req.max_open_positions is not None:
            rm.max_open_positions = req.max_open_positions
            updated["max_open_positions"] = req.max_open_positions

        logger.info(f"Risk config runtime update: {updated}")
        return {
            "success": True,
            "updated": updated,
            "data": rm.get_risk_summary(trader.portfolio.total_usdt),
        }
    except Exception as e:
        logger.error(f"update_risk_config hatasi: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# PAPER TRADING (BLOK 2C)
# ============================================================
def _paper():
    """Lazy paper trader access."""
    from bot.paper_trader import get_paper_trader
    return get_paper_trader()


@router.get("/paper/status")
async def paper_status():
    """Paper trader özeti (bakiye, açık pozisyonlar, PnL)."""
    try:
        return {"success": True, "data": _paper().get_paper_summary()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/paper/trades")
async def paper_trades(limit: int = Query(50, ge=1, le=500)):
    """In-memory paper trade geçmişi (session bazlı)."""
    try:
        return {"success": True, "data": _paper().get_paper_trades(limit=limit)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/paper/start")
async def paper_start():
    """Paper trading motorunu başlat."""
    try:
        await _paper().start()
        return {"success": True, "message": "Paper trader baslatildi"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/paper/stop")
async def paper_stop():
    """Paper trading motorunu durdur."""
    try:
        await _paper().stop()
        return {"success": True, "message": "Paper trader durduruldu"}
    except Exception as e:
        return {"success": False, "error": str(e)}


class PaperResetRequest(BaseModel):
    balance: Optional[float] = Field(None, gt=0, le=1_000_000_000)


@router.post("/paper/reset")
async def paper_reset(req: Optional[PaperResetRequest] = None):
    """Paper bakiyeyi sıfırla (opsiyonel yeni bakiye)."""
    try:
        new_bal = req.balance if req else None
        _paper().reset(new_balance=new_bal)
        return {"success": True, "data": _paper().get_paper_summary()}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# PRICE ALARMS (BLOK 7C)
# ============================================================
class AlarmRequest(BaseModel):
    symbol: str = Field(..., min_length=3, max_length=20)
    price: float = Field(..., gt=0)
    direction: str = Field(..., pattern="^(above|below)$")


@router.get("/alarms")
async def get_alarms():
    """Aktif fiyat alarmları listesi."""
    return {"success": True, "data": _price_alarms}


@router.post("/alarms")
async def set_alarm(req: AlarmRequest):
    """Yeni fiyat alarmı ekle.

    `_price_updater` içinde live_prices her güncellendiğinde alarmlar
    kontrol edilir (notify_event ile ilişkili değil - trader'a eklenecek
    hook aşağıda).
    """
    global _alarm_id_counter
    _alarm_id_counter += 1
    symbol = req.symbol.upper()
    if "/" not in symbol and symbol.endswith("USDT"):
        symbol = symbol[:-4] + "/USDT"
    alarm = {
        "id": _alarm_id_counter,
        "symbol": symbol,
        "price": req.price,
        "direction": req.direction,
        "created_at": time.time(),
        "triggered": False,
    }
    _price_alarms.append(alarm)
    logger.info(f"Alarm eklendi: {alarm}")
    return {"success": True, "data": alarm}


@router.delete("/alarms/{alarm_id}")
async def delete_alarm(alarm_id: int):
    """Alarmı sil."""
    global _price_alarms
    before = len(_price_alarms)
    _price_alarms[:] = [a for a in _price_alarms if a["id"] != alarm_id]
    removed = before - len(_price_alarms)
    return {"success": True, "removed": removed}


def check_price_alarms(prices: Dict[str, float]) -> List[Dict[str, Any]]:
    """Her fiyat güncellemesi sonrası alarm kontrolü.

    Tetiklenen alarmları döndürür ve `_price_alarms` listesinden siler
    (tek seferlik tetikleme mantığı). Trader/_price_updater ve ws broadcast
    tarafında bu fonksiyon çağrılır.
    """
    global _price_alarms
    triggered: List[Dict[str, Any]] = []
    remaining: List[Dict[str, Any]] = []
    for a in _price_alarms:
        p = prices.get(a["symbol"])
        if p is None:
            remaining.append(a)
            continue
        if a["direction"] == "above" and p >= a["price"]:
            a["triggered_price"] = p
            a["triggered_at"] = time.time()
            triggered.append(a)
        elif a["direction"] == "below" and p <= a["price"]:
            a["triggered_price"] = p
            a["triggered_at"] = time.time()
            triggered.append(a)
        else:
            remaining.append(a)
    _price_alarms = remaining
    return triggered
