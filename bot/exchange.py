"""
OKX Exchange Entegrasyonu (ccxt üzerinden)
Async wrapper with retry logic and error handling.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import ccxt
import ccxt.async_support as ccxt_async

from config import Config

logger = logging.getLogger(__name__)


class Exchange:
    """OKX borsası için async istemci sarmalayıcısı"""

    def __init__(self):
        self.config = Config.CCXT_CONFIG.copy()
        self._client: Optional[ccxt_async.okx] = None
        self._sync_client: Optional[ccxt.okx] = None
        self._lock = asyncio.Lock()
        self._markets_loaded = False

    async def connect(self):
        """OKX bağlantısını başlat"""
        async with self._lock:
            if self._client is not None:
                return
            try:
                self._client = ccxt_async.okx(self.config)
                if Config.OKX_SANDBOX:
                    self._client.set_sandbox_mode(True)
                await self._client.load_markets()
                self._markets_loaded = True
                logger.info(f"OKX baglantisi basarili (sandbox={Config.OKX_SANDBOX})")
            except Exception as e:
                logger.error(f"OKX baglanti hatasi: {e}")
                raise

    async def close(self):
        """Bağlantıyı kapat"""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as e:
                logger.warning(f"Baglanti kapatma hatasi: {e}")
            self._client = None

    async def _ensure_connected(self):
        if self._client is None:
            await self.connect()

    async def _safe_call(self, func_name: str, *args, max_retries: int = 3, **kwargs):
        """Retry mantığıyla güvenli API çağrısı.

        Geçici hatalarda exponential backoff, kalıcı hatalarda exception fırlatır.
        """
        await self._ensure_connected()
        last_err: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                method = getattr(self._client, func_name)
                return await method(*args, **kwargs)
            except ccxt.RateLimitExceeded as e:
                last_err = e
                wait = 2 ** attempt
                logger.warning(f"Rate limit asildi, {wait}s bekleniyor")
                await asyncio.sleep(wait)
            except ccxt.NetworkError as e:
                last_err = e
                logger.warning(f"Network hatasi ({attempt+1}/{max_retries}): {e}")
                await asyncio.sleep(1 + attempt)
            except ccxt.ExchangeError as e:
                # Business-level exchange error (ör. InsufficientFunds, InvalidOrder)
                # -> retry yapma, hemen propagate et
                logger.error(f"Exchange hatasi [{func_name}]: {e}")
                raise
            except Exception as e:
                last_err = e
                logger.error(f"Beklenmedik hata [{func_name}] (deneme {attempt+1}): {e}")
                await asyncio.sleep(1)
        logger.error(f"[{func_name}] {max_retries} denemeden sonra basarisiz")
        # last_err mutlaka set olacak (ya da yukarıda already raised)
        raise last_err if last_err else RuntimeError(f"{func_name} failed without exception")

    # ============================================================
    # PUBLIC API
    # ============================================================
    async def get_balance(self) -> Dict[str, Any]:
        """Hesap bakiyesini getir ve USDT karşılığını hesapla."""
        try:
            bal = await self._safe_call("fetch_balance")
            if not bal:
                return {"total": {}, "free": {}, "used": {}, "usdt_value": 0.0}
            result = {
                "total": {},
                "free": {},
                "used": {},
                "usdt_value": 0.0,
            }
            for cur, amounts in bal.get("total", {}).items():
                if amounts and amounts > 0:
                    result["total"][cur] = float(amounts)
                    result["free"][cur] = float(bal.get("free", {}).get(cur, 0))
                    result["used"][cur] = float(bal.get("used", {}).get(cur, 0))

            # USDT karşılığı hesapla - önce USDT'yi say, sonra non-USDT için
            # tek seferde toplu ticker al (rate limit dostu)
            usdt_total = float(result["total"].get("USDT", 0))
            non_usdt_currencies = [c for c in result["total"].keys() if c != "USDT"]
            if non_usdt_currencies:
                symbols = [f"{c}/USDT" for c in non_usdt_currencies]
                try:
                    tickers = await self._safe_call("fetch_tickers", symbols)
                    for c in non_usdt_currencies:
                        sym = f"{c}/USDT"
                        t = (tickers or {}).get(sym)
                        if t and t.get("last"):
                            usdt_total += result["total"][c] * float(t["last"])
                except Exception as e:
                    # Fallback: tek tek al
                    logger.debug(f"fetch_tickers fallback: {e}")
                    for c in non_usdt_currencies:
                        try:
                            ticker = await self.get_ticker(f"{c}/USDT")
                            if ticker:
                                usdt_total += result["total"][c] * ticker["last"]
                        except Exception:
                            pass
            result["usdt_value"] = round(usdt_total, 4)
            return result
        except Exception as e:
            logger.error(f"get_balance hatasi: {e}")
            return {"total": {}, "free": {}, "used": {}, "usdt_value": 0.0, "error": str(e)}

    async def get_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Sembol için anlık ticker"""
        try:
            t = await self._safe_call("fetch_ticker", symbol)
            return {
                "symbol": symbol,
                "last": float(t["last"]),
                "bid": float(t.get("bid") or 0),
                "ask": float(t.get("ask") or 0),
                "high": float(t.get("high") or 0),
                "low": float(t.get("low") or 0),
                "volume": float(t.get("baseVolume") or 0),
                "change_pct": float(t.get("percentage") or 0),
                "timestamp": t.get("timestamp"),
            }
        except Exception as e:
            logger.error(f"get_ticker hatasi [{symbol}]: {e}")
            return None

    async def get_tickers(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Birden fazla sembol için ticker - toplu istek ile rate-limit dostu."""
        if not symbols:
            return {}
        try:
            raw = await self._safe_call("fetch_tickers", symbols)
            if not raw:
                return {}
            result: Dict[str, Dict[str, Any]] = {}
            for sym, t in raw.items():
                if not t:
                    continue
                last = t.get("last") or t.get("close") or 0
                result[sym] = {
                    "symbol": sym,
                    "last": float(last or 0),
                    "bid": float(t.get("bid") or 0),
                    "ask": float(t.get("ask") or 0),
                    "volume": float(t.get("baseVolume") or 0),
                }
            return result
        except Exception as e:
            # Toplu çağrı başarısızsa tek tek dene (fallback)
            logger.warning(f"fetch_tickers basarisiz, tek tek deneniyor: {e}")
            tickers: Dict[str, Dict[str, Any]] = {}
            for sym in symbols:
                t = await self.get_ticker(sym)
                if t:
                    tickers[sym] = t
            return tickers

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "15m", limit: int = 200
    ) -> List[List]:
        """OHLCV mum verilerini getir"""
        try:
            return await self._safe_call(
                "fetch_ohlcv", symbol, timeframe, limit=limit
            )
        except Exception as e:
            logger.error(f"get_ohlcv hatasi [{symbol} {timeframe}]: {e}")
            return []

    async def get_spot_markets(self) -> List[str]:
        """OKX'deki aktif USDT spot paritelerini dondur."""
        try:
            await self._ensure_connected()
            if not self._client:
                return []
            markets = await self._safe_call("load_markets")
            if not markets:
                return []
            symbols = [
                sym for sym, m in markets.items()
                if m.get("active") and m.get("spot") and sym.endswith("/USDT")
            ]
            return sorted(symbols)
        except Exception as e:
            logger.warning(f"get_spot_markets hatasi: {e}")
            return []

    async def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        order_type: str = "market",
    ) -> Optional[Dict[str, Any]]:
        """Emir ver (market veya limit).

        Döndürülen değer: ccxt order dict. Başarısızlıkta None.
        """
        try:
            side = (side or "").lower()
            order_type = (order_type or "market").lower()

            if side not in ("buy", "sell"):
                raise ValueError(f"Gecersiz side: {side}")
            if order_type not in ("market", "limit"):
                raise ValueError(f"Gecersiz order_type: {order_type}")
            if amount is None or amount <= 0:
                raise ValueError(f"Gecersiz emir miktari: {amount}")
            if order_type == "limit" and (price is None or price <= 0):
                raise ValueError("Limit emir icin gecerli price gerekli")

            if order_type == "market":
                order = await self._safe_call(
                    "create_order", symbol, "market", side, amount
                )
            else:
                order = await self._safe_call(
                    "create_order", symbol, "limit", side, amount, price
                )
            if not order:
                logger.error(f"Emir response bos: {symbol} {side} {amount}")
                return None
            logger.info(
                f"EMIR VERILDI: {side.upper()} {amount} {symbol} @ "
                f"{price or 'MARKET'} [{order.get('id')}]"
            )
            return order
        except Exception as e:
            logger.error(f"place_order hatasi: {e}")
            return None

    async def cancel_order(
        self, order_id: str, symbol: str
    ) -> Optional[Dict[str, Any]]:
        """Emri iptal et"""
        try:
            res = await self._safe_call("cancel_order", order_id, symbol)
            logger.info(f"Emir iptal edildi: {order_id}")
            return res
        except Exception as e:
            logger.error(f"cancel_order hatasi: {e}")
            return None

    async def get_open_orders(
        self, symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Açık emirler"""
        try:
            return await self._safe_call("fetch_open_orders", symbol)
        except Exception as e:
            logger.error(f"get_open_orders hatasi: {e}")
            return []

    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Emir geçmişi"""
        try:
            return await self._safe_call(
                "fetch_closed_orders", symbol, limit=limit
            )
        except Exception as e:
            logger.error(f"get_order_history hatasi: {e}")
            return []

    async def get_positions(
        self, symbols: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Açık pozisyonlar (futures)"""
        try:
            positions = await self._safe_call("fetch_positions", symbols)
            return [p for p in positions if p.get("contracts", 0) > 0]
        except Exception as e:
            logger.debug(f"get_positions hatasi (spot hesapta normaldir): {e}")
            return []

    async def get_market_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Pazar bilgileri (min amount, precision, vb.)"""
        await self._ensure_connected()
        if not self._markets_loaded:
            try:
                await self._client.load_markets()
                self._markets_loaded = True
            except Exception as e:
                logger.error(f"load_markets hatasi: {e}")
                return None
        try:
            market = self._client.market(symbol)
            return {
                "symbol": market["symbol"],
                "base": market["base"],
                "quote": market["quote"],
                "active": market.get("active", True),
                "precision": market.get("precision", {}),
                "limits": market.get("limits", {}),
            }
        except Exception as e:
            logger.error(f"get_market_info hatasi [{symbol}]: {e}")
            return None
