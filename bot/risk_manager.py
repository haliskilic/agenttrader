"""
Risk Yönetimi Modülü
- Pozisyon boyutlandırma
- Stop loss / Take profit
- Günlük kayıp limiti
- Maksimum açık pozisyon sayısı
- Drawdown koruması
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from config import Config
from database import db

logger = logging.getLogger(__name__)


@dataclass
class RiskCheck:
    """Risk kontrol sonucu"""
    approved: bool
    reason: str = ""
    adjusted_amount: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    metadata: Dict = field(default_factory=dict)


class RiskManager:
    """Trading için risk yönetim kontrolcüsü"""

    def __init__(self):
        self.max_position_pct = Config.MAX_POSITION_SIZE_PCT
        self.stop_loss_pct = Config.STOP_LOSS_PCT
        self.take_profit_pct = Config.TAKE_PROFIT_PCT
        self.max_daily_loss_pct = Config.MAX_DAILY_LOSS_PCT
        self.max_open_positions = Config.MAX_OPEN_POSITIONS
        self.min_order_value = Config.MIN_ORDER_VALUE

        # Session tracking
        self.initial_balance: Optional[float] = None
        self.peak_balance: Optional[float] = None
        self._current_balance: Optional[float] = None
        self._paused_due_to_risk = False

        # Trailing stop seviyeleri (symbol -> trail price). Pozisyon başına bir
        # trailing SL tutulur; fiyat lehe ilerledikçe yukarı çekilir, aleyhe
        # gidince GERİ ÇEKİLMEZ (trailing mantığı).
        self._trailing_stops: Dict[str, float] = {}

    def update_balance_snapshot(self, current_balance: float):
        """Bakiye snapshot'larını güncelle (drawdown + günlük kayıp için).

        initial_balance artık gün bazında DB'ye kaydedilir. Bot restart
        edilse bile aynı gün içindeki referans korunur; ertesi gün yeni
        snapshot alınır. Bu sayede check_daily_loss_limit gerçekçi çalışır.
        """
        try:
            today_key = f"daily_start_balance_{date.today().isoformat()}"
            stored = db.get_state(today_key)
            if stored is None:
                # Gün içi ilk çağrı -> bugünün başlangıç bakiyesi olarak kaydet
                db.set_state(today_key, str(current_balance))
                self.initial_balance = current_balance
                logger.info(
                    f"Risk Manager gunluk baslangic bakiyesi: {current_balance} USDT"
                )
            else:
                try:
                    self.initial_balance = float(stored)
                except (ValueError, TypeError):
                    logger.warning(f"Bozuk daily_start_balance '{stored}' - sifirlaniyor")
                    db.set_state(today_key, str(current_balance))
                    self.initial_balance = current_balance
        except Exception as e:
            # DB hatası durumunda önceki davranışa düş
            logger.error(f"update_balance_snapshot DB hatasi: {e}")
            if self.initial_balance is None:
                self.initial_balance = current_balance

        # Peak balance - drawdown için (persist etmiyoruz, session bazlı)
        if self.peak_balance is None or current_balance > self.peak_balance:
            self.peak_balance = current_balance

        # Son görülen bakiye - test ve raporlama için
        self._current_balance = current_balance

    def calculate_position_size(
        self, balance_usdt: float, price: float, custom_pct: Optional[float] = None
    ) -> float:
        """Portföyün yüzdesine göre pozisyon boyutu hesapla.

        ÖNEMLİ: `max(value_usdt, min_order_value)` kullanmak düşük bakiyede
        kullanıcının istediğinden fazla pozisyon açılmasına neden olur. Bunun
        yerine min_order_value'yi yalnızca validate_order içinde uygulayıp
        gerekirse bakiyeyle sınırlarız. Burada yalnızca yüzdelik boyut döndürülür.
        """
        if price <= 0 or balance_usdt <= 0:
            return 0.0
        pct = custom_pct if custom_pct is not None else self.max_position_pct
        value_usdt = balance_usdt * pct
        amount = value_usdt / price
        return round(amount, 6)

    def calculate_stop_loss(self, entry_price: float, side: str) -> float:
        """Stop loss fiyatını hesapla"""
        if side.lower() == "buy":
            return round(entry_price * (1 - self.stop_loss_pct), 8)
        return round(entry_price * (1 + self.stop_loss_pct), 8)

    def calculate_take_profit(self, entry_price: float, side: str) -> float:
        """Take profit fiyatını hesapla"""
        if side.lower() == "buy":
            return round(entry_price * (1 + self.take_profit_pct), 8)
        return round(entry_price * (1 - self.take_profit_pct), 8)

    def check_daily_loss_limit(self) -> Tuple[bool, float]:
        """Günlük kayıp limitini kontrol et.

        NOT: initial_balance bot başlangıcında set ediliyor - gün ortasında
        bot yeniden başlatılırsa bu referans DEĞİŞİR. Daha sağlam bir çözüm
        için peak_balance veya gün başındaki bakiye DB'de saklanmalı (TODO).
        """
        try:
            daily_pnl = db.get_daily_pnl()
            if self.initial_balance and self.initial_balance > 0:
                loss_pct = abs(daily_pnl) / self.initial_balance if daily_pnl < 0 else 0.0
                if loss_pct >= self.max_daily_loss_pct:
                    return False, loss_pct
                return True, loss_pct
            return True, 0.0
        except Exception as e:
            logger.error(f"Daily loss check hatasi: {e}")
            return True, 0.0

    def check_drawdown(self, current_balance: float) -> Tuple[bool, float]:
        """Drawdown kontrolü"""
        if not self.peak_balance or self.peak_balance <= 0:
            return True, 0.0
        drawdown = (self.peak_balance - current_balance) / self.peak_balance
        if drawdown >= Config.MAX_DRAWDOWN_PCT:
            return False, drawdown
        return True, drawdown

    def check_open_positions(self, current_open: int) -> bool:
        """Açık pozisyon limiti"""
        return current_open < self.max_open_positions

    def validate_order(
        self,
        symbol: str,
        side: str,
        proposed_amount: float,
        price: float,
        balance_usdt: float,
        open_positions: int,
        current_balance: float,
    ) -> RiskCheck:
        """
        Emri tüm risk kriterleri açısından doğrula.
        Sonuç: RiskCheck (approved veya red).
        """
        # 1. Günlük kayıp limiti
        ok, loss_pct = self.check_daily_loss_limit()
        if not ok:
            return RiskCheck(
                approved=False,
                reason=f"Gunluk kayip limiti asildi: {loss_pct*100:.2f}%",
            )

        # 2. Drawdown
        ok, dd = self.check_drawdown(current_balance)
        if not ok:
            return RiskCheck(
                approved=False,
                reason=f"Max drawdown asildi: {dd*100:.2f}%",
            )

        # 3. Açık pozisyon limiti
        if not self.check_open_positions(open_positions):
            return RiskCheck(
                approved=False,
                reason=f"Max acik pozisyon limitine ulasildi ({open_positions})",
            )

        # Giriş değerlerini geçersiz olmayacak şekilde koru
        if price <= 0 or proposed_amount <= 0:
            return RiskCheck(
                approved=False,
                reason=f"Gecersiz fiyat/miktar: price={price}, amount={proposed_amount}",
            )

        original_amount = proposed_amount

        # 4. Maksimum pozisyon boyutu (önce yüksek sınırı uygula)
        max_value = balance_usdt * self.max_position_pct
        if proposed_amount * price > max_value:
            adjusted = max_value / price
            proposed_amount = round(adjusted, 6)
            logger.info(
                f"Pozisyon boyutu max_position_pct'e gore ayarlandi: {proposed_amount}"
            )

        # 5. Minimum işlem miktarı (ayarlamadan SONRA tekrar kontrol et)
        order_value = proposed_amount * price
        if order_value < self.min_order_value:
            # Min emir değeri max_position_pct'ten büyükse RED
            if self.min_order_value > balance_usdt * self.max_position_pct:
                return RiskCheck(
                    approved=False,
                    reason=(
                        f"Minimum emir degeri ({self.min_order_value} USDT) "
                        f"max pozisyon boyutunu asar ({max_value:.2f} USDT)"
                    ),
                )
            # Minimum emir değerini yakalamaya çalış (hâlâ bakiyeyi aşmamalı)
            adjusted = self.min_order_value / price
            if adjusted * price > balance_usdt:
                return RiskCheck(
                    approved=False,
                    reason=f"Minimum emir degeri icin yetersiz bakiye",
                )
            proposed_amount = round(adjusted, 6)

        # 6. Yeterli bakiye kontrolü (buy için USDT)
        # Küçük bir buffer (%1) bırak ki komisyon sığabilsin
        required_usdt = proposed_amount * price * 1.01
        if side.lower() == "buy" and balance_usdt < required_usdt:
            return RiskCheck(
                approved=False,
                reason=f"Yetersiz USDT bakiye: {balance_usdt:.4f} < {required_usdt:.4f}",
            )

        sl = self.calculate_stop_loss(price, side)
        tp = self.calculate_take_profit(price, side)

        return RiskCheck(
            approved=True,
            reason="OK",
            adjusted_amount=proposed_amount,
            stop_loss=sl,
            take_profit=tp,
            metadata={
                "original_amount": original_amount,
                "final_amount": proposed_amount,
                "order_value_usdt": round(proposed_amount * price, 4),
                "balance_usdt": balance_usdt,
            },
        )

    # ============================================================
    # TRAILING STOP
    # ============================================================
    def update_trailing_stop(
        self, symbol: str, current_price: float, side: str,
        trail_pct: float = 0.015,
    ) -> Optional[float]:
        """Trailing stop seviyesini güncelle ve güncel değeri döndür.

        Parameters
        ----------
        symbol : str
        current_price : float
            Güncel fiyat (live_prices'tan gelir).
        side : str
            "long" veya "short".
        trail_pct : float
            Trailing yüzdesi (default %1.5 - yani fiyatın %1.5 altı trail SL).

        Returns
        -------
        Optional[float]
            Güncel trailing SL fiyatı. Henüz kurulmamışsa ilk değeri oluşturur.
            "short" destek: trailing SL price'ın üstünde tutulur ve düştükçe
            aşağı çekilir.
        """
        if current_price is None or current_price <= 0 or trail_pct <= 0:
            return self._trailing_stops.get(f"trail_{symbol}")

        key = f"trail_{symbol}"
        current_trail = self._trailing_stops.get(key)

        if side == "long":
            new_sl = current_price * (1 - trail_pct)
            # Trailing: sadece yukarı çekilir, aşağı gelmez
            if current_trail is None or new_sl > current_trail:
                self._trailing_stops[key] = new_sl
                return new_sl
            return current_trail

        if side == "short":
            new_sl = current_price * (1 + trail_pct)
            if current_trail is None or new_sl < current_trail:
                self._trailing_stops[key] = new_sl
                return new_sl
            return current_trail

        return current_trail

    def get_trailing_stop(self, symbol: str) -> Optional[float]:
        """Kayıtlı trailing stop değerini al (yoksa None)."""
        return self._trailing_stops.get(f"trail_{symbol}")

    def clear_trailing_stop(self, symbol: str):
        """Pozisyon kapandığında trailing kaydını temizle."""
        self._trailing_stops.pop(f"trail_{symbol}", None)

    def get_risk_summary(self, current_balance: float) -> Dict:
        """Risk durumu özeti"""
        ok_daily, loss_pct = self.check_daily_loss_limit()
        ok_dd, dd = self.check_drawdown(current_balance)
        return {
            "initial_balance": self.initial_balance,
            "peak_balance": self.peak_balance,
            "current_balance": current_balance,
            "daily_loss_pct": round(loss_pct * 100, 2),
            "drawdown_pct": round(dd * 100, 2),
            "daily_loss_ok": ok_daily,
            "drawdown_ok": ok_dd,
            "max_position_size_pct": self.max_position_pct * 100,
            "stop_loss_pct": self.stop_loss_pct * 100,
            "take_profit_pct": self.take_profit_pct * 100,
            "max_daily_loss_pct": self.max_daily_loss_pct * 100,
            "max_open_positions": self.max_open_positions,
        }
