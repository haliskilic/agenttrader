"""
AgentTrader - Gelistirilmis Backtester
Parametreli SL/TP/pozisyon buyuklugu, genisletilmis metrikler, demo modu.

Geriye uyumluluk notu
---------------------
Constructor iki farkli sekilde cagrilabilir:
  Backtester(exchange, strategies_dict)   -> eski API (dict: name -> strategy instance)
  Backtester(exchange=exchange)           -> yeni API (strategies STRATEGY_REGISTRY'den alinir)

Her iki durumda da `run()` ayni imzayla calisir.
"""

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from .strategies.base import SignalType

logger = logging.getLogger(__name__)


# ============================================================
# DATACLASSES
# ============================================================
@dataclass
class BtTrade:
    side: str               # "buy" veya "sell"
    entry_price: float
    exit_price: float
    amount: float
    entry_ts: datetime
    exit_ts: datetime
    duration_bars: int
    reason: str             # strategy_buy, stop_loss, take_profit, strategy_sell, end_of_data
    pnl: float
    pnl_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            # Genisletilmis alanlar
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "amount": self.amount,
            "entry_ts": self.entry_ts.isoformat() if self.entry_ts else None,
            "exit_ts": self.exit_ts.isoformat() if self.exit_ts else None,
            "duration_bars": self.duration_bars,
            "reason": self.reason,
            "pnl": round(self.pnl, 4),
            "pnl_pct": round(self.pnl_pct, 4),
            # Geriye uyumluluk: eski trade dict alanlari
            "price": self.exit_price,
            "timestamp": self.exit_ts.isoformat() if self.exit_ts else None,
        }


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    timeframe: str
    initial_balance: float
    final_balance: float
    total_return_pct: float
    total_trades: int
    winners: int
    losers: int
    win_rate: float
    profit_factor: Optional[float]         # None => tum trade'ler karli
    avg_win: float
    avg_loss: float
    max_consecutive_losses: int
    avg_trade_duration_bars: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: Optional[float]          # None => max_drawdown == 0
    max_drawdown_pct: float
    recovery_factor: Optional[float]       # None => max_drawdown == 0
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[BtTrade] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)
    demo_mode: bool = False
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    position_size_pct: float = 0.05
    candle_count: int = 0
    start: str = ""
    end: str = ""

    def to_dict(self) -> Dict[str, Any]:
        def _safe(v):
            if v is None:
                return None
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            return v

        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
            "initial_balance": self.initial_balance,
            "final_balance": round(self.final_balance, 2),
            "total_return_pct": round(self.total_return_pct, 4),
            # Geriye uyumluluk: eski anahtarlar
            "total_pnl": round(self.final_balance - self.initial_balance, 4),
            "total_pnl_pct": round(self.total_return_pct, 2),
            "total_trades": self.total_trades,
            "winners": self.winners,
            "losers": self.losers,
            "win_trades": self.winners,
            "loss_trades": self.losers,
            "win_rate": round(self.win_rate, 2),
            "profit_factor": _safe(self.profit_factor),
            "avg_win": round(self.avg_win, 4),
            "avg_loss": round(self.avg_loss, 4),
            "max_consecutive_losses": self.max_consecutive_losses,
            "avg_trade_duration_bars": round(self.avg_trade_duration_bars, 1),
            "sharpe_ratio": round(_safe(self.sharpe_ratio) or 0.0, 4),
            "sharpe": round(_safe(self.sharpe_ratio) or 0.0, 3),  # alias
            "sortino_ratio": round(_safe(self.sortino_ratio) or 0.0, 4),
            "calmar_ratio": _safe(self.calmar_ratio),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "max_drawdown": round(self.max_drawdown_pct, 2),  # alias
            "recovery_factor": _safe(self.recovery_factor),
            "equity_curve": self.equity_curve,
            "trades": [t.to_dict() for t in self.trades],
            "signals": self.signals,
            "demo_mode": self.demo_mode,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "position_size_pct": self.position_size_pct,
            "candle_count": self.candle_count,
            "start": self.start,
            "end": self.end,
        }


# ============================================================
# BACKTESTER
# ============================================================
class Backtester:
    """
    Strateji bazli backtester.

    - Parametreli SL/TP/pozisyon buyuklugu
    - Bar-ici SL/TP simulasyonu (high/low kullanir)
    - Genisletilmis metrikler: Sharpe, Sortino, Calmar, profit_factor, consecutive losses
    - demo_mode: Exchange baglantisi olmadan `ohlcv_data` ile calistir
    """

    def __init__(
        self,
        exchange: Any = None,
        strategies: Optional[Dict[str, Any]] = None,
    ):
        self.exchange = exchange
        # strategies dict dogrudan verilmisse kullan; aksi halde run() icinde
        # STRATEGY_REGISTRY'den instance olusturulur.
        self.strategies: Dict[str, Any] = strategies or {}

    async def run(
        self,
        symbol: str,
        timeframe: str,
        strategy_name: str,
        initial_balance: float = 1000.0,
        limit: int = 500,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.04,
        position_size_pct: float = 0.05,
        demo_mode: bool = False,
        ohlcv_data: Optional[List[List]] = None,
    ) -> BacktestResult:
        """Backtest calistir.

        Parametreler
        ------------
        ohlcv_data : Optional[List[List]]
            Onceden cekilmis OHLCV verisi. demo_mode icin veya birden fazla
            stratejiyi ayni veri uzerinde karsilastirmak icin kullan.
        """
        # --- Strateji resolve (her backtest icin taze instance) ---
        # Canli bot strategy instance'larini kullanmiyoruz: stateful cooldown/bar
        # tracking canlidan backteste sizabildigi icin her seferinde yeni instance.
        try:
            from .strategies import STRATEGY_REGISTRY
            StrategyClass = STRATEGY_REGISTRY.get(strategy_name)
            if not StrategyClass:
                raise ValueError(f"Bilinmeyen strateji: {strategy_name}")
            strategy = StrategyClass({"symbols": [symbol], **_strategy_cfg(strategy_name)})
        except ImportError as e:
            raise ValueError(f"Strateji yuklenemedi: {strategy_name} ({e})")

        # --- OHLCV ---
        if ohlcv_data is not None:
            ohlcv = ohlcv_data
        elif self.exchange is not None:
            ohlcv = await self.exchange.get_ohlcv(symbol, timeframe, limit=limit)
        else:
            raise ValueError(
                "Exchange baglantisi yok ve ohlcv_data saglanmadi. "
                "demo_mode icin ohlcv_data gerekli."
            )

        min_candles = getattr(strategy, "min_candles", 50)
        if not ohlcv or len(ohlcv) < max(min_candles + 5, 50):
            raise ValueError(f"Yetersiz veri: {len(ohlcv) if ohlcv else 0} mum")

        # --- Simulasyon state ---
        balance = initial_balance
        peak_equity = initial_balance
        max_drawdown_pct = 0.0
        position: Optional[Dict[str, Any]] = None
        trades: List[BtTrade] = []
        equity_curve: List[Dict[str, Any]] = []
        signals: List[Dict[str, Any]] = []
        consecutive_losses = 0
        max_consecutive_losses = 0

        for i in range(min_candles, len(ohlcv)):
            window = ohlcv[:i]
            candle = ohlcv[i]
            ts_ms, o, h, l, c, vol = candle
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
            ts_unix = int(ts_ms / 1000)

            # --- SL/TP kontrolu (bar ici) ---
            if position:
                entry = position["entry_price"]
                sl_price = entry * (1 - stop_loss_pct)
                tp_price = entry * (1 + take_profit_pct)
                exit_price: Optional[float] = None
                exit_reason: str = ""

                if l <= sl_price:
                    exit_price = sl_price
                    exit_reason = "stop_loss"
                elif h >= tp_price:
                    exit_price = tp_price
                    exit_reason = "take_profit"

                if exit_price is not None:
                    amt = position["amount"]
                    pnl = (exit_price - entry) * amt
                    pnl_pct = (exit_price - entry) / entry * 100
                    balance += amt * exit_price

                    trade = BtTrade(
                        side="sell",
                        entry_price=entry,
                        exit_price=exit_price,
                        amount=amt,
                        entry_ts=position["entry_ts"],
                        exit_ts=ts,
                        duration_bars=i - position["entry_bar"],
                        reason=exit_reason,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                    )
                    trades.append(trade)
                    signals.append({
                        "time": ts_unix,
                        "type": "sell",
                        "price": exit_price,
                        "reason": exit_reason,
                        "strategy": strategy_name,
                        "executed": True,
                    })

                    if pnl < 0:
                        consecutive_losses += 1
                        max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                    else:
                        consecutive_losses = 0

                    position = None

            # --- Strateji sinyali ---
            try:
                signal = await strategy.generate_signal(symbol, window)
            except Exception as e:
                logger.debug(f"BT sinyal hatasi mum {i}: {e}")
                signal = None

            if signal and signal.type != SignalType.HOLD:
                executed = False
                sig_type_str = signal.type.value if hasattr(signal.type, "value") else str(signal.type)

                if signal.type == SignalType.BUY and not position and balance > 0:
                    invest = balance * position_size_pct
                    if invest >= 5.0:  # min 5 USDT trade
                        amount = invest / c
                        balance -= invest
                        position = {
                            "entry_price": c,
                            "amount": amount,
                            "entry_bar": i,
                            "entry_ts": ts,
                        }
                        executed = True

                elif signal.type == SignalType.SELL and position:
                    entry = position["entry_price"]
                    amt = position["amount"]
                    pnl = (c - entry) * amt
                    pnl_pct = (c - entry) / entry * 100
                    balance += amt * c

                    trades.append(BtTrade(
                        side="sell",
                        entry_price=entry,
                        exit_price=c,
                        amount=amt,
                        entry_ts=position["entry_ts"],
                        exit_ts=ts,
                        duration_bars=i - position["entry_bar"],
                        reason="strategy_sell",
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                    ))

                    if pnl < 0:
                        consecutive_losses += 1
                        max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                    else:
                        consecutive_losses = 0

                    position = None
                    executed = True

                signals.append({
                    "time": ts_unix,
                    "type": sig_type_str,
                    "signal_type": sig_type_str,  # alias (charts.js uyumu)
                    "price": c,
                    "reason": signal.reason,
                    "strategy": strategy_name,
                    "strength": getattr(signal, "strength", 1.0),
                    "executed": executed,
                    "timestamp": ts.isoformat(),
                })

            # --- Equity & drawdown ---
            current_equity = balance + (position["amount"] * c if position else 0)
            equity_curve.append({"time": ts_unix, "value": round(current_equity, 2)})

            peak_equity = max(peak_equity, current_equity)
            if peak_equity > 0:
                dd = (peak_equity - current_equity) / peak_equity * 100
                max_drawdown_pct = max(max_drawdown_pct, dd)

        # --- Acik pozisyonu kapat ---
        if position:
            last_c = ohlcv[-1][4]
            last_ts_ms = ohlcv[-1][0]
            last_ts = datetime.fromtimestamp(last_ts_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
            entry = position["entry_price"]
            amt = position["amount"]
            pnl = (last_c - entry) * amt
            pnl_pct = (last_c - entry) / entry * 100
            balance += amt * last_c

            trades.append(BtTrade(
                side="sell",
                entry_price=entry,
                exit_price=last_c,
                amount=amt,
                entry_ts=position["entry_ts"],
                exit_ts=last_ts,
                duration_bars=len(ohlcv) - 1 - position["entry_bar"],
                reason="end_of_data",
                pnl=pnl,
                pnl_pct=pnl_pct,
            ))

            if pnl < 0:
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

        # --- Metrikler ---
        total_trades = len(trades)
        winners_list = [t for t in trades if t.pnl > 0]
        losers_list = [t for t in trades if t.pnl <= 0]
        winners = len(winners_list)
        losers = len(losers_list)
        win_rate = (winners / total_trades * 100) if total_trades else 0.0

        avg_win = (sum(t.pnl for t in winners_list) / winners) if winners else 0.0
        avg_loss = (sum(t.pnl for t in losers_list) / losers) if losers else 0.0

        gross_profit = sum(t.pnl for t in winners_list)
        gross_loss = abs(sum(t.pnl for t in losers_list))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

        avg_duration = (
            sum(t.duration_bars for t in trades) / total_trades
        ) if total_trades else 0.0

        pnl_series = [t.pnl_pct for t in trades]
        sharpe = _sharpe(pnl_series)
        sortino = _sortino(pnl_series)

        total_return_pct = (
            (balance - initial_balance) / initial_balance * 100
        ) if initial_balance else 0.0

        # Calmar = annualized_return / max_drawdown (doğru formül)
        # Recovery Factor = total_return / max_drawdown (annualize edilmez)
        period_days = (ohlcv[-1][0] - ohlcv[0][0]) / (1000 * 86400)
        annualized_return_pct = (
            total_return_pct * (365 / period_days) if period_days > 0 else total_return_pct
        )
        calmar = (
            round(annualized_return_pct / max_drawdown_pct, 4)
            if max_drawdown_pct > 0 else None
        )
        recovery = (
            round(total_return_pct / max_drawdown_pct, 4)
            if max_drawdown_pct > 0 else None
        )

        start_ts = datetime.fromtimestamp(ohlcv[min_candles][0] / 1000).isoformat()
        end_ts = datetime.fromtimestamp(ohlcv[-1][0] / 1000).isoformat()

        return BacktestResult(
            symbol=symbol,
            strategy=strategy_name,
            timeframe=timeframe,
            initial_balance=initial_balance,
            final_balance=balance,
            total_return_pct=total_return_pct,
            total_trades=total_trades,
            winners=winners,
            losers=losers,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            max_consecutive_losses=max_consecutive_losses,
            avg_trade_duration_bars=avg_duration,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            max_drawdown_pct=max_drawdown_pct,
            recovery_factor=recovery,
            equity_curve=equity_curve,
            trades=trades,
            signals=signals,
            demo_mode=demo_mode,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            position_size_pct=position_size_pct,
            candle_count=len(ohlcv),
            start=start_ts,
            end=end_ts,
        )


# ============================================================
# YARDIMCI FONKSIYONLAR
# ============================================================
def _strategy_cfg(name: str) -> dict:
    """Config.STRATEGY_CONFIGS'ten strateji konfigurasyonunu guvenli sekilde al."""
    try:
        from config import Config
        return Config.STRATEGY_CONFIGS.get(name, {}) or {}
    except Exception:
        return {}


def _sharpe(returns: List[float], risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio. Trade-bazli return serisi (%) uzerinden."""
    if len(returns) < 2:
        return 0.0
    mean = statistics.mean(returns) - risk_free
    std = statistics.stdev(returns)
    if std == 0:
        return 0.0
    return mean / std * (252 ** 0.5)


def _sortino(returns: List[float], risk_free: float = 0.0) -> float:
    """Annualized Sortino ratio (downside deviation)."""
    if len(returns) < 2:
        return 0.0
    mean = statistics.mean(returns) - risk_free
    neg = [r for r in returns if r < 0]
    if not neg:
        return 0.0
    downside_std = (sum(r ** 2 for r in neg) / len(neg)) ** 0.5
    if downside_std == 0:
        return 0.0
    return mean / downside_std * (252 ** 0.5)
