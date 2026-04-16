/*
 * AgentTrader - Chart Modulu
 * TradingView Lightweight Charts entegrasyonu + Sinyal Marker'lari
 */

// Strateji bazli marker stilleri
const STRATEGY_STYLES = {
  rsi_bb:       { buyColor: '#3fb950', sellColor: '#f85149', buyShape: 'arrowUp',   sellShape: 'arrowDown' },
  macd_signal:  { buyColor: '#58a6ff', sellColor: '#ffa657', buyShape: 'circle',    sellShape: 'circle'    },
  stoch_rsi:    { buyColor: '#a371f7', sellColor: '#e06c75', buyShape: 'arrowUp',   sellShape: 'arrowDown' },
  ma_crossover: { buyColor: '#e3b341', sellColor: '#d29922', buyShape: 'circle',    sellShape: 'circle'    },
  grid_trading: { buyColor: '#39d353', sellColor: '#da3633', buyShape: 'square',    sellShape: 'square'    },
};
const DEFAULT_STYLE = { buyColor: '#3fb950', sellColor: '#f85149', buyShape: 'arrowUp', sellShape: 'arrowDown' };

const ChartManager = (() => {
  let chart = null;
  let candleSeries = null;
  let volumeSeries = null;
  let currentSymbol = "BTC/USDT";
  let currentTimeframe = "15m";
  // Key: `${ts}_${type}_${strategy}` -> marker objesi
  let markerMap = {};
  // Ayri LightweightCharts instance (equity egrisi icin)
  let _equityChart = null;

  function init() {
    const container = document.getElementById("chart");
    if (!container) {
      console.error("chart container bulunamadi");
      return;
    }
    container.innerHTML = "";

    chart = LightweightCharts.createChart(container, {
      layout: {
        background: { color: "#0d1117" },
        textColor: "#e6edf3",
      },
      grid: {
        vertLines: { color: "#21262d" },
        horzLines: { color: "#21262d" },
      },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#30363d" },
      timeScale: {
        borderColor: "#30363d",
        timeVisible: true,
        secondsVisible: false,
      },
      width: container.clientWidth,
      height: container.clientHeight || 400,
    });

    candleSeries = chart.addCandlestickSeries({
      upColor: "#3fb950",
      downColor: "#f85149",
      borderUpColor: "#3fb950",
      borderDownColor: "#f85149",
      wickUpColor: "#3fb950",
      wickDownColor: "#f85149",
    });

    volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      color: "#30363d",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    window.addEventListener("resize", () => {
      if (chart && container) {
        chart.applyOptions({
          width: container.clientWidth,
          height: container.clientHeight || 400,
        });
      }
      if (_equityChart) {
        const eqContainer = document.getElementById("equityChart");
        if (eqContainer) {
          _equityChart.applyOptions({ width: eqContainer.clientWidth || 400 });
        }
      }
    });
  }

  async function loadData(symbol, timeframe) {
    currentSymbol = symbol;
    currentTimeframe = timeframe;
    markerMap = {};
    try {
      const [base, quote] = symbol.split("/");
      const key = localStorage.getItem("agenttrader_key") || "";
      const res = await fetch(
        `/api/ohlcv/${base}/${quote}/${timeframe}?limit=200`,
        { headers: key ? { "X-API-Key": key } : {} }
      );
      const json = await res.json();
      if (!json.success || !json.data) {
        console.error("OHLCV alinamadi", json);
        return;
      }
      const candles = json.data.map((c) => ({
        time: c.time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }));
      const volumes = json.data.map((c) => ({
        time: c.time,
        value: c.volume,
        color: c.close >= c.open ? "rgba(63, 185, 80, 0.3)" : "rgba(248, 81, 73, 0.3)",
      }));
      candleSeries.setData(candles);
      volumeSeries.setData(volumes);
      chart.timeScale().fitContent();

      // Mevcut sinyalleri yukle
      await loadSignalMarkers(symbol);
    } catch (e) {
      console.error("loadData hatasi", e);
    }
  }

  // Aktif sinyal filtrelerini oku (checkbox durumlarindan)
  function _getActiveFilters() {
    const boxes = document.querySelectorAll('.signal-filter');
    if (!boxes.length) return null; // filtre UI yok -> hepsini goster
    const active = new Set();
    boxes.forEach(cb => { if (cb.checked) active.add(cb.value); });
    return active;
  }

  async function loadSignalMarkers(symbol) {
    try {
      const key = localStorage.getItem("agenttrader_key") || "";
      const res = await fetch(
        `/api/signals?symbol=${encodeURIComponent(symbol)}&limit=200`,
        { headers: key ? { "X-API-Key": key } : {} }
      );
      const json = await res.json();
      if (!json.success || !json.data) return;

      const activeFilters = _getActiveFilters();
      for (const sig of json.data) {
        if (activeFilters && sig.strategy && !activeFilters.has(sig.strategy)) continue;
        const ts = Math.floor(new Date(sig.timestamp).getTime() / 1000);
        addMarkerToMap(ts, sig.signal_type, sig.strategy, sig.executed, sig.strength || 1);
      }
      _applyMarkers();
    } catch (e) {
      console.error("Signal marker yukleme hatasi", e);
    }
  }

  function addMarkerToMap(ts, type, strategy, executed, strength) {
    const key = `${ts}_${type}_${strategy || 'default'}`;
    const style = STRATEGY_STYLES[strategy] || DEFAULT_STYLE;
    const isBuy = type === 'buy';
    const color = isBuy ? style.buyColor : style.sellColor;
    const shape = isBuy ? style.buyShape : style.sellShape;
    const size = (strength && strength >= 2) ? 2 : 1;

    markerMap[key] = {
      time: ts,
      position: isBuy ? 'belowBar' : 'aboveBar',
      color: executed ? color : color + '88',
      shape,
      text: `${(strategy || '').toUpperCase().substring(0, 4)} ${isBuy ? '▲' : '▼'}`,
      size,
    };
  }

  function _applyMarkers() {
    if (!candleSeries) return;
    const markers = Object.values(markerMap).sort((a, b) => a.time - b.time);
    try {
      candleSeries.setMarkers(markers);
    } catch (e) {
      console.error("setMarkers hatasi", e);
    }
  }

  // Canli sinyal geldiginde marker ekle (WebSocket'ten cagrilir)
  function addLiveSignal(signal) {
    if (!signal || !signal.symbol || signal.symbol !== currentSymbol) return;
    const activeFilters = _getActiveFilters();
    if (activeFilters && signal.strategy && !activeFilters.has(signal.strategy)) return;
    const ts = Math.floor(new Date(signal.timestamp).getTime() / 1000);
    addMarkerToMap(ts, signal.type, signal.strategy, signal.executed || false, signal.strength || 1);
    _applyMarkers();
  }

  // Backtest sinyallerini goster
  function showBacktestSignals(signals) {
    markerMap = {};
    for (const s of signals) {
      // Backtest signals: `time` (unix) ve `type` / `signal_type`
      const ts = s.time || (s.timestamp ? Math.floor(new Date(s.timestamp).getTime() / 1000) : null);
      if (!ts) continue;
      const type = s.signal_type || s.type;
      const strategy = s.strategy || 'backtest';
      addMarkerToMap(ts, type, strategy, s.executed, s.strength || 1);
    }
    _applyMarkers();
  }

  // Filtre degistiginde sinyal marker'lari yeniden yukle
  async function reloadSignalsWithFilter(symbol) {
    markerMap = {};
    await loadSignalMarkers(symbol || currentSymbol);
    _applyMarkers();
  }

  // Ayri LightweightCharts instance uzerinde equity egrisini ciz
  function showEquityCurvePanel(equityData, timeLabels) {
    const panel = document.getElementById('equityPanel');
    const container = document.getElementById('equityChart');
    if (!panel || !container) return;
    panel.style.display = 'block';

    // Varsa eski equity chart'i temizle
    if (_equityChart) {
      try { _equityChart.remove(); } catch (e) {}
      _equityChart = null;
    }

    _equityChart = LightweightCharts.createChart(container, {
      width: container.clientWidth || 400,
      height: 150,
      layout: { background: { color: '#0d1117' }, textColor: '#c9d1d9' },
      grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      timeScale: { visible: true, timeVisible: true },
      rightPriceScale: { visible: true },
      crosshair: { mode: 1 },
    });

    const lineSeries = _equityChart.addLineSeries({
      color: '#58a6ff',
      lineWidth: 2,
      priceLineVisible: false,
    });

    // equityData iki farkli formatta olabilir:
    //  - [{time, value}, ...]  (backend'in dondurdugu format)
    //  - [number, number, ...] + timeLabels array
    let seriesData;
    if (equityData.length > 0 && typeof equityData[0] === 'object') {
      seriesData = equityData
        .map((d, i) => ({
          time: d.time || (timeLabels ? timeLabels[i] : i + 1),
          value: d.value,
        }))
        .filter(d => d.time != null && d.value != null);
    } else {
      seriesData = equityData
        .map((v, i) => ({
          time: timeLabels ? timeLabels[i] : i + 1,
          value: v,
        }))
        .filter(d => d.time != null && d.value != null);
    }

    if (seriesData.length > 0) {
      lineSeries.setData(seriesData);
      _equityChart.timeScale().fitContent();
    }
  }

  function clearEquityPanel() {
    if (_equityChart) {
      try { _equityChart.remove(); } catch (e) {}
      _equityChart = null;
    }
    const panel = document.getElementById('equityPanel');
    if (panel) panel.style.display = 'none';
    const tradeTable = document.getElementById('btTradeTable');
    if (tradeTable) { tradeTable.style.display = 'none'; tradeTable.innerHTML = ''; }
  }

  // Geriye uyumluluk: eski showEquityCurve() ayri panele delege eder
  function showEquityCurve(equityData) {
    showEquityCurvePanel(equityData);
  }

  function clearBacktest() {
    clearEquityPanel();
    // Backtest signal marker'larini temizle, canli sinyalleri geri yukle
    markerMap = {};
    loadSignalMarkers(currentSymbol);
  }

  function updateLastPrice(price) {
    // Gercek zamanli fiyat guncelleme icin rezerve
  }

  return {
    init,
    loadData,
    addLiveSignal,
    showBacktestSignals,
    showEquityCurve,
    showEquityCurvePanel,
    clearEquityPanel,
    clearBacktest,
    reloadSignalsWithFilter,
    updateLastPrice,
    getCurrentSymbol: () => currentSymbol,
    getCurrentTimeframe: () => currentTimeframe,
  };
})();
