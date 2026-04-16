"""
WebSocket Handler
- Gerçek zamanlı fiyat güncellemeleri
- Bot durumu, trade bildirimleri, PnL
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from config import Config
from database import db


def _utcnow() -> datetime:
    """UTC timestamp - _utcnow() deprecated in Python 3.12+."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


logger = logging.getLogger(__name__)
router = APIRouter()


class WebSocketManager:
    """Bağlı WebSocket istemcilerini yönet.

    asyncio.Lock, modül yüklenme sırasında event loop yokken yaratılırsa
    Python 3.10+'da DeprecationWarning verir. Bu yüzden lazy init kullanırız.
    """

    def __init__(self):
        self.active: Set[WebSocket] = set()
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._get_lock():
            self.active.add(ws)
        logger.info(f"WS baglandi, toplam: {len(self.active)}")

    async def disconnect(self, ws: WebSocket):
        async with self._get_lock():
            self.active.discard(ws)
        logger.info(f"WS koptu, kalan: {len(self.active)}")

    async def broadcast(self, message: dict):
        if not self.active:
            return
        payload = json.dumps(message, default=str)
        dead = []
        async with self._get_lock():
            clients = list(self.active)
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._get_lock():
                for ws in dead:
                    self.active.discard(ws)

    async def send_to(self, ws: WebSocket, message: dict):
        try:
            await ws.send_text(json.dumps(message, default=str))
        except Exception as e:
            logger.error(f"WS send hatasi: {e}")


ws_manager = WebSocketManager()


def _trader():
    from bot.trader import get_trader
    return get_trader()


# Daily PnL için küçük bir cache - her saniye DB sorgusu atmaktan kaçın.
_cached_daily_pnl: float = 0.0
_cached_pnl_ts: float = 0.0


async def _periodic_broadcast():
    """Her saniye fiyat + durum gönder"""
    global _cached_daily_pnl, _cached_pnl_ts
    import time as _time
    while True:
        try:
            await asyncio.sleep(1)
            if not ws_manager.active:
                continue
            try:
                trader = _trader()
                # Daily PnL cache (10s TTL) - portfolio summary içinde override edilir
                now = _time.time()
                if now - _cached_pnl_ts > 10:
                    try:
                        _cached_daily_pnl = db.get_daily_pnl()
                    except Exception as e:
                        logger.debug(f"daily_pnl cache refresh hatasi: {e}")
                    _cached_pnl_ts = now
                portfolio_summary = trader.portfolio.get_summary()
                # DB'den tazelemek yerine cache değerini kullan (kritik değil, tasarruf)
                portfolio_summary["daily_pnl"] = round(_cached_daily_pnl, 4)
                msg = {
                    "type": "tick",
                    "timestamp": _utcnow().isoformat(),
                    "prices": trader.live_prices,
                    "status": trader.status.value,
                    "portfolio": portfolio_summary,
                }
                await ws_manager.broadcast(msg)
            except Exception as e:
                logger.debug(f"Broadcast icerik hatasi: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Periodic broadcast hatasi: {e}")


_broadcast_task: Optional[asyncio.Task] = None
# asyncio.Lock() modül yüklenmesi sırasında oluşturulursa event loop
# henüz mevcut olmayabilir -> RuntimeError. Lazy init kullanırız.
_broadcast_lock: Optional[asyncio.Lock] = None


async def start_broadcast_loop():
    """Background periyodik broadcast loop (thread-safe)."""
    global _broadcast_task, _broadcast_lock
    if _broadcast_lock is None:
        _broadcast_lock = asyncio.Lock()
    async with _broadcast_lock:
        if _broadcast_task is None or _broadcast_task.done():
            _broadcast_task = asyncio.create_task(
                _periodic_broadcast(), name="ws_broadcast"
            )


async def notify_event(data: dict):
    """Trader -> WebSocket notification callback"""
    data["type"] = data.get("type", "event")
    data["timestamp"] = _utcnow().isoformat()
    await ws_manager.broadcast(data)


@router.websocket("/ws/live")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
):
    """Live veri WebSocket'i.

    GÜVENLİK: WEB_SECRET_KEY tanımlı ise ?token=... query param zorunlu.
    """
    # Auth - SECRET_KEY varsa zorunlu
    if Config.WEB_AUTH_ENABLED:
        if token != Config.WEB_SECRET_KEY:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            logger.warning(
                f"WS unauthorized connect from {websocket.client.host if websocket.client else '?'}"
            )
            return

    await ws_manager.connect(websocket)
    await start_broadcast_loop()

    # Bağlantı kurulunca ilk hello paketi
    try:
        trader = _trader()
        await ws_manager.send_to(websocket, {
            "type": "hello",
            "timestamp": _utcnow().isoformat(),
            "status": trader.status.value,
            "symbols": trader.symbols,
            "strategies": trader.get_strategies_info(),
        })
    except Exception as e:
        logger.error(f"WS hello hatasi: {e}")

    try:
        while True:
            # Client ping veya komut alabilir
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws_manager.send_to(websocket, {"type": "pong"})
                elif msg.get("type") == "subscribe":
                    # İsteğe bağlı kanal takibi
                    await ws_manager.send_to(websocket, {
                        "type": "subscribed",
                        "channel": msg.get("channel"),
                    })
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WS endpoint hatasi: {e}")
        await ws_manager.disconnect(websocket)
