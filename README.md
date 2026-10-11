# AgentTrader

[![Lisans: AGPL-3.0](https://img.shields.io/badge/Lisans-AGPL--3.0-blue.svg)](LICENSE) [![Ticari lisans](https://img.shields.io/badge/Ticari-lisans%20mevcut-green.svg)](COMMERCIAL_LICENSE.md) [![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/haliskilic)

OKX borsası üzerinde çalışan, web dashboard ve Telegram bildirimleri içeren tam otomatik kripto para trading botu.

## Özellikler

- **Çoklu Strateji:** RSI+Bollinger Bands, MA Crossover, Grid Trading, MACD Signal, Stochastic RSI
- **Paper Trading:** Gerçek para riske atmadan canlı fiyatlarla simülasyon
- **Web Dashboard:** FastAPI tabanlı gerçek zamanlı izleme paneli (WebSocket)
- **Telegram Bildirimleri:** İşlem ve uyarı bildirimleri
- **Risk Yönetimi:** Stop-loss, take-profit, günlük kayıp limiti, max drawdown koruması
- **OKX Entegrasyonu:** CCXT ile spot/swap/futures desteği

## Gereksinimler

- Python 3.11+
- OKX hesabı ve API anahtarları
- (Opsiyonel) Telegram Bot Token

## Kurulum

### 1. Repoyu klonlayın

```bash
git clone https://github.com/haliskilic/agenttrader.git
cd agenttrader
```

### 2. Sanal ortam oluşturun

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate
```

### 3. Bağımlılıkları yükleyin

```bash
pip install -r requirements.txt
```

### 4. Ortam değişkenlerini yapılandırın

`.env.example` dosyasını kopyalayın ve düzenleyin:

```bash
cp .env.example .env
```

`.env` dosyasını doldurun:

```env
# OKX API Bilgileri
OKX_API_KEY=your_api_key_here
OKX_SECRET_KEY=your_secret_key_here
OKX_PASSPHRASE=your_passphrase_here
OKX_SANDBOX=false          # Test için true yapın

# Telegram (opsiyonel)
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Web Dashboard
WEB_HOST=127.0.0.1
WEB_PORT=8080
WEB_SECRET_KEY=change_this_to_a_random_secret_32chars
WEB_AUTH_ENABLED=true

# Paper Trading (gerçek emir vermez)
PAPER_TRADING=false
PAPER_BALANCE=10000.0
```

### 5. OKX API Anahtarı Oluşturma

1. [OKX](https://www.okx.com) hesabınıza giriş yapın
2. **Hesap → API Yönetimi → API Oluştur** yolunu izleyin
3. İzinler: **Okuma** + **İşlem** (çekim izni vermeyin)
4. IP kısıtlaması eklemeniz önerilir

## Kullanım

### Botu Başlatma

```bash
python main.py
```

Web dashboard otomatik olarak `http://127.0.0.1:8080` adresinde açılır.

### Paper Trading (Test Modu)

Gerçek para riske atmadan test etmek için:

```env
PAPER_TRADING=true
PAPER_BALANCE=10000.0
```

### Strateji Seçimi

`config.py` içinde `STRATEGY_CONFIGS` altında istediğiniz stratejiyi `"enabled": true` yapın:

```python
"rsi_bb": {
    "enabled": True,       # Bu stratejiyi aktif et
    "timeframe": "15m",
    "rsi_period": 14,
    ...
}
```

## Proje Yapısı

```
agenttrader/
├── main.py              # Ana başlangıç noktası
├── config.py            # Merkezi konfigürasyon
├── database.py          # SQLAlchemy modelleri ve DB işlemleri
├── requirements.txt     # Python bağımlılıkları
├── .env.example         # Örnek ortam değişkenleri
│
├── bot/                 # Trading motoru
│   ├── trader.py        # Ana trader (canlı işlemler)
│   ├── paper_trader.py  # Paper trading simülatörü
│   ├── exchange.py      # OKX/CCXT bağlantısı
│   ├── risk_manager.py  # Risk yönetimi
│   ├── portfolio.py     # Portföy takibi
│   ├── backtester.py    # Backtesting modülü
│   └── strategies/      # Trading stratejileri
│       ├── rsi_bb.py
│       ├── ma_crossover.py
│       ├── grid_trading.py
│       ├── macd_signal.py
│       └── stoch_rsi.py
│
├── web/                 # Web dashboard
│   ├── app.py           # FastAPI uygulaması
│   ├── routes/          # API endpoint'leri
│   └── static/          # HTML/CSS/JS arayüzü
│
└── telegram_bot/        # Telegram bildirimleri
    └── bot.py
```

## Stratejiler

| Strateji | Zaman Dilimi | Açıklama |
|----------|-------------|----------|
| `rsi_bb` | 15m | RSI aşırı alım/satım + Bollinger Bands kırılımı |
| `ma_crossover` | 1h | EMA 9/21 kesişim sinyali |
| `grid_trading` | 5m | Fiyat aralığında grid emirleri |
| `macd_signal` | 1h | MACD + EMA 200 trend filtresi |
| `stoch_rsi` | 30m | Stochastic RSI momentum |

## Risk Yönetimi (Varsayılan)

| Parametre | Değer | Açıklama |
|-----------|-------|----------|
| Max pozisyon büyüklüğü | %5 | Portföyün en fazla %5'i tek işlemde |
| Stop-loss | %2 | Zararı durdurma |
| Take-profit | %4 | Kar alma |
| Günlük max kayıp | %10 | Aşılınca bot durur |
| Max açık pozisyon | 5 | Aynı anda en fazla 5 pozisyon |
| Max drawdown | %20 | Toplam düşüş limiti |

`config.py` içindeki `RISK MANAGEMENT` bölümünden değiştirilebilir.

## Testler

```bash
pytest tests/ -v
```

## Güvenlik Notları

- `.env` dosyasını asla commit etmeyin (`.gitignore` ile korunuyor)
- OKX API'ye çekim izni **vermeyin**
- `WEB_SECRET_KEY` değerini güçlü, rastgele bir değere ayarlayın
- Uzak sunucuya deploy ederken `WEB_HOST=0.0.0.0` yapın ve firewall kuralı ekleyin

## Lisans

AgentTrader **çift lisanslıdır**:

- **Açık kaynak — [GNU AGPL-3.0](LICENSE):** kullanmak, incelemek, değiştirmek ve paylaşmak serbesttir. Dağıtırsan ya da değiştirilmiş bir sürümü ağ servisi olarak çalıştırırsan, tüm kaynak kodunu AGPL-3.0 ile sunman gerekir.
- **Ticari lisans:** AGPL-3.0 yükümlülükleri olmadan kapalı kaynak ürün, cihaz ya da SaaS için — bkz. [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) veya **mail@haliskilic.com.tr**.

Katkılar memnuniyetle karşılanır! İlk pull request'inde tek seferlik bir [Katkıcı Lisans Sözleşmesi (CLA)](https://gist.github.com/haliskilic/7a7fdcd355f54d1db6943959d7b76823) imzalaman istenir; katkının telif hakkı sende kalır.

Telif © 2026 Halis KILIÇ.

## Projeyi destekle

AgentTrader işine yarıyorsa **[GitHub Sponsors üzerinden destek olmayı ❤️](https://github.com/sponsors/haliskilic)** düşünebilirsin — sponsorluklar test donanımını ve projenin bakımına ayrılan zamanı karşılıyor.

GitHub hesabın yoksa **[bir kahve ısmarlayabilirsin ☕](https://buymeacoffee.com/haliskilic)**.
