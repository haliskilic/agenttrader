/*
 * AgentTrader - Ana Dashboard JS
 * WebSocket, API cagrilari ve UI guncellemeleri
 */

const App = (() => {
  const state = {
    ws: null,
    connected: false,
    botStatus: "stopped",
    symbols: [],
    strategies: [],
    logs: [],
    reconnectAttempts: 0,
    apiKey: localStorage.getItem("agenttrader_key") || "",
  };

  function getApiKey() {
    if (!state.apiKey) {
      const key = prompt("API anahtarını girin (WEB_SECRET_KEY):");
      if (key) {
        state.apiKey = key.trim();
        localStorage.setItem("agenttrader_key", state.apiKey);
      }
    }
    return state.apiKey;
  }

  // ============================================================
  // WEBSOCKET
  // ============================================================
  function connectWs() {
    const wsProto = location.protocol === "https:" ? "wss" : "ws";
    const key = getApiKey();
    const url = `${wsProto}://${location.host}/ws/live${key ? "?token=" + encodeURIComponent(key) : ""}`;
    state.ws = new WebSocket(url);

    state.ws.onopen = () => {
      state.connected = true;
      state.reconnectAttempts = 0;
      addLog("INFO", "WS baglandi");
    };

    state.ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        handleWsMessage(msg);
      } catch (e) {
        console.error("WS parse hatasi", e);
      }
    };

    state.ws.onclose = () => {
      state.connected = false;
      addLog("WARNING", "WS baglanti koptu, yeniden deneniyor...");
      setTimeout(
        connectWs,
        Math.min(30000, 1000 * Math.pow(2, state.reconnectAttempts++))
      );
    };

    state.ws.onerror = (e) => {
      console.error("WS hatasi", e);
    };
  }

  function handleWsMessage(msg) {
    switch (msg.type) {
      case "hello":
        state.symbols = msg.symbols || [];
        state.strategies = msg.strategies || [];
        updateStatusBadge(msg.status);
        renderStrategies();
        break;
      case "tick":
        updatePrices(msg.prices || {});
        updatePortfolio(msg.portfolio);
        updateStatusBadge(msg.status);
        break;
      case "trade_executed":
        showToast(
          `Trade: ${msg.trade.side.toUpperCase()} ${msg.trade.amount} ${msg.trade.symbol} @ ${msg.trade.price}`,
          "success"
        );
        addLog("SUCCESS", `${msg.trade.side.toUpperCase()} ${msg.trade.symbol} @ ${msg.trade.price}`);
        refreshTrades();
        break;
      case "position_closed":
        const pnl = msg.trade.pnl;
        const sign = pnl >= 0 ? "+" : "";
        showToast(
          `Pozisyon kapandi: ${msg.trade.symbol} PnL: ${sign}${pnl.toFixed(4)} USDT`,
          pnl >= 0 ? "success" : "error"
        );
        addLog(pnl >= 0 ? "SUCCESS" : "WARNING", `Close ${msg.trade.symbol} PnL: ${sign}${pnl.toFixed(4)}`);
        refreshTrades();
        break;
      case "bot_started":
        addLog("SUCCESS", "Bot baslatildi");
        break;
      case "bot_stopped":
        addLog("WARNING", "Bot durduruldu");
        break;
      case "bot_paused":
        addLog("WARNING", "Bot duraklatildi");
        break;
      case "price_alarm":
        if (msg.alarm) {
          const a = msg.alarm;
          const arrow = a.direction === "above" ? "≥" : "≤";
          showToast(`ALARM: ${a.symbol} ${arrow} ${a.price} (şu an: ${a.triggered_price?.toFixed(4)})`, "success");
          addLog("SUCCESS", `Alarm tetiklendi: ${a.symbol} ${arrow} ${a.price}`);
          loadAlarms();
        }
        break;
      case "paper_trade_executed":
      case "paper_position_closed":
      case "paper_started":
      case "paper_stopped":
        refreshPaperStatus();
        break;
      case "signal":
        // Gercek zamanli sinyal -> grafik marker ekle
        if (msg.signal) {
          ChartManager.addLiveSignal(msg.signal);
          const stype = msg.signal.type === "buy" ? "AL" : "SAT";
          const color = msg.signal.type === "buy" ? "success" : "error";
          addLog(msg.signal.type === "buy" ? "SUCCESS" : "WARNING",
            `${stype} SİNYALİ: ${msg.signal.symbol} @ ${msg.signal.price?.toFixed(2)} [${msg.signal.strategy}]`);
          showToast(`${stype}: ${msg.signal.symbol} @ ${msg.signal.price?.toFixed(2)}`, color);
        }
        break;
      case "pong":
        break;
    }
  }

  // ============================================================
  // API CAGRILARI
  // ============================================================
  async function api(path, options = {}) {
    try {
      const key = getApiKey();
      const headers = { "Content-Type": "application/json" };
      if (key) headers["X-API-Key"] = key;
      const res = await fetch(`/api${path}`, { headers, ...options, headers: { ...headers, ...(options.headers || {}) } });
      if (res.status === 401) {
        localStorage.removeItem("agenttrader_key");
        state.apiKey = "";
        showToast("API anahtarı geçersiz, yeniden girin", "error");
        return { success: false, error: "Unauthorized" };
      }
      return await res.json();
    } catch (e) {
      console.error("API hatasi", path, e);
      return { success: false, error: e.message };
    }
  }

  // ============================================================
  // MARKETS (OKX spot pariteleri)
  // ============================================================
  async function loadMarkets() {
    try {
      const res = await api("/markets");
      if (!res || !res.success || !res.symbols || !res.symbols.length) return;

      // symbolDatalist ve btSymbolDatalist'i doldur
      ["symbolDatalist", "btSymbolDatalist"].forEach((id) => {
        const dl = document.getElementById(id);
        if (!dl) return;
        dl.innerHTML = "";
        res.symbols.forEach((sym) => {
          const opt = document.createElement("option");
          opt.value = sym;
          dl.appendChild(opt);
        });
      });

      // alarmSymbol ve moSymbol hala <select>; onlari da doldur
      ["alarmSymbol", "moSymbol"].forEach((id) => {
        const sel = document.getElementById(id);
        if (!sel) return;
        const currentVal = sel.value;
        sel.innerHTML = "";
        res.symbols.forEach((sym) => {
          const opt = document.createElement("option");
          opt.value = sym;
          opt.textContent = sym;
          sel.appendChild(opt);
        });
        if (res.symbols.includes(currentVal)) sel.value = currentVal;
      });
    } catch (e) {
      console.warn("loadMarkets hatasi:", e);
    }
  }

  // ============================================================
  // UI UPDATE
  // ============================================================
  function updateStatusBadge(status) {
    state.botStatus = status;
    const indicator = document.getElementById("statusIndicator");
    const text = document.getElementById("statusText");
    if (!indicator || !text) return;
    indicator.classList.remove("running", "paused", "stopped");
    if (status === "running") {
      indicator.classList.add("running");
      text.textContent = "CALISIYOR";
    } else if (status === "paused") {
      indicator.classList.add("paused");
      text.textContent = "DURAKLATILDI";
    } else {
      indicator.classList.add("stopped");
      text.textContent = "DURDU";
    }
  }

  function updatePrices(prices) {
    const sel = document.getElementById("symbolSelect");
    const selected = sel ? sel.value : "BTC/USDT";
    if (prices[selected]) {
      const priceEl = document.getElementById("currentPrice");
      if (priceEl) {
        priceEl.textContent = prices[selected].toFixed(2);
      }
    }
  }

  function updatePortfolio(portfolio) {
    if (!portfolio) return;

    const balEl = document.getElementById("balanceValue");
    if (balEl) balEl.textContent = (portfolio.total_usdt || 0).toFixed(2);

    const freeEl = document.getElementById("balanceFree");
    if (freeEl) freeEl.textContent = `Serbest: ${(portfolio.free_usdt || 0).toFixed(2)}`;

    const unrEl = document.getElementById("unrealizedPnl");
    if (unrEl) {
      const v = portfolio.unrealized_pnl || 0;
      unrEl.textContent = (v >= 0 ? "+" : "") + v.toFixed(4);
      unrEl.style.color = v >= 0 ? "var(--success)" : "var(--danger)";
    }

    const dpEl = document.getElementById("dailyPnl");
    if (dpEl) {
      const v = portfolio.daily_pnl || 0;
      dpEl.textContent = (v >= 0 ? "+" : "") + v.toFixed(4);
      dpEl.style.color = v >= 0 ? "var(--success)" : "var(--danger)";
    }

    const posCount = document.getElementById("positionsCount");
    if (posCount)
      posCount.textContent = portfolio.open_positions_count || 0;

    renderPositions(portfolio.open_positions || []);
  }

  function renderPositions(positions) {
    const list = document.getElementById("positionsList");
    if (!list) return;
    if (!positions.length) {
      list.innerHTML = '<div class="empty">Acik pozisyon yok</div>';
      return;
    }
    list.innerHTML = positions
      .map((p) => {
        const pnlClass = (p.unrealized_pnl || 0) >= 0 ? "positive" : "negative";
        const sign = (p.unrealized_pnl || 0) >= 0 ? "+" : "";
        const slHtml = p.stop_loss
          ? `<div style="font-size:10px;color:var(--text-secondary);">SL: ${p.stop_loss.toFixed(4)}</div>`
          : "";
        const tpHtml = p.take_profit
          ? `<div style="font-size:10px;color:var(--text-secondary);">TP: ${p.take_profit.toFixed(4)}</div>`
          : "";
        return `
        <div class="position-item">
          <div>
            <div class="symbol">${p.symbol}</div>
            <div style="font-size:10px;color:var(--text-secondary)">
              <span class="side-${p.side}">${p.side.toUpperCase()}</span>
              ${p.amount} @ ${p.entry_price.toFixed(2)}
            </div>
            ${slHtml}${tpHtml}
          </div>
          <div class="trade-pnl ${pnlClass}">
            ${sign}${(p.unrealized_pnl || 0).toFixed(4)}
            <div style="font-size:10px;">${(p.unrealized_pnl_pct || 0).toFixed(2)}%</div>
          </div>
        </div>`;
      })
      .join("");
  }

  function renderStrategies() {
    const list = document.getElementById("strategiesList");
    if (!list) return;
    list.innerHTML = state.strategies
      .map((s) => {
        let sigHtml = "";
        const sigs = Object.values(s.last_signals || {});
        if (sigs.length) {
          const lastSig = sigs[sigs.length - 1];
          sigHtml = `<span class="strategy-signal ${lastSig.type}">${lastSig.type.toUpperCase()}</span>`;
        }
        return `
        <div class="strategy-item">
          <div class="strategy-info">
            <div class="strategy-name">
              ${s.name}
              <span class="tf-badge" style="display:inline-block;margin-left:6px;padding:1px 6px;background:var(--bg-secondary,#222);border-radius:4px;font-size:10px;color:var(--text-secondary);">${s.timeframe}</span>
            </div>
            <div class="strategy-meta">
              Semboller: ${(s.symbols || []).join(", ")}
            </div>
          </div>
          <div class="strategy-controls">
            ${sigHtml}
            <div class="toggle ${s.enabled ? "active" : ""}" data-strategy="${s.name}"></div>
          </div>
        </div>`;
      })
      .join("");

    // Toggle eventleri
    list.querySelectorAll(".toggle").forEach((el) => {
      el.addEventListener("click", async () => {
        const name = el.dataset.strategy;
        const res = await api(`/strategies/${name}/toggle`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        if (res.success) {
          el.classList.toggle("active");
          addLog("INFO", `Strateji ${name}: ${res.data.enabled ? "aktif" : "pasif"}`);
          refreshStrategies();
        }
      });
    });
  }

  async function refreshStrategies() {
    const res = await api("/strategies");
    if (res.success) {
      state.strategies = res.data;
      renderStrategies();
    }
  }

  async function refreshTrades() {
    const res = await api("/trades?limit=15");
    if (!res.success) return;
    const list = document.getElementById("tradesList");
    if (!list) return;
    if (!res.data.length) {
      list.innerHTML = '<div class="empty">Trade yok</div>';
      return;
    }
    list.innerHTML = res.data
      .map((t) => {
        const pnlClass = (t.pnl || 0) >= 0 ? "positive" : "negative";
        const sign = (t.pnl || 0) >= 0 ? "+" : "";
        const time = new Date(t.timestamp).toLocaleTimeString("tr-TR");
        return `
        <div class="trade-item">
          <div>
            <div class="symbol">
              <span class="side-${t.side}">${t.side.toUpperCase()}</span>
              ${t.symbol}
            </div>
            <div class="trade-price">${t.amount} @ ${t.price.toFixed(2)} · ${time}</div>
          </div>
          <div class="trade-pnl ${pnlClass}">
            ${t.pnl ? sign + t.pnl.toFixed(4) : "-"}
          </div>
        </div>`;
      })
      .join("");
  }

  async function refreshPnl() {
    const res = await api("/pnl?days=30");
    if (!res.success) return;
    const d = res.data;
    const stats = d.stats || {};
    const metrics = d.metrics || {};

    const total = (stats.total_pnl || 0) + (d.unrealized || 0);
    const totalEl = document.getElementById("totalPnl");
    if (totalEl) {
      totalEl.textContent = (total >= 0 ? "+" : "") + total.toFixed(4);
      totalEl.className = "pnl-value " + (total >= 0 ? "positive" : "negative");
    }

    const wr = document.getElementById("winRate");
    if (wr) wr.textContent = (stats.win_rate || 0).toFixed(2) + "%";

    const tt = document.getElementById("totalTrades");
    if (tt) tt.textContent = stats.total_trades || 0;

    const sr = document.getElementById("sharpeRatio");
    if (sr) sr.textContent = (metrics.sharpe_ratio || 0).toFixed(3);

    const pf = document.getElementById("profitFactor");
    if (pf) pf.textContent = metrics.profit_factor ? metrics.profit_factor.toFixed(3) : "-";
  }

  async function refreshRisk() {
    const res = await api("/risk");
    if (!res.success) return;
    const r = res.data;
    document.getElementById("maxPositionPct").textContent = r.max_position_size_pct + "%";
    document.getElementById("stopLossPct").textContent = r.stop_loss_pct + "%";
    document.getElementById("takeProfitPct").textContent = r.take_profit_pct + "%";
    document.getElementById("dailyLossPct").textContent = r.daily_loss_pct.toFixed(2) + "%";
    document.getElementById("drawdownPct").textContent = r.drawdown_pct.toFixed(2) + "%";
  }

  // ============================================================
  // LOGS
  // ============================================================
  function addLog(level, message) {
    const container = document.getElementById("logsContainer");
    if (!container) return;
    const now = new Date().toLocaleTimeString("tr-TR");
    const entry = document.createElement("div");
    entry.className = "log-entry";
    entry.innerHTML = `<span class="log-time">[${now}]</span> <span class="log-level-${level}">${level}</span> ${message}`;
    container.prepend(entry);
    while (container.children.length > 100) {
      container.removeChild(container.lastChild);
    }
  }

  // ============================================================
  // TOAST
  // ============================================================
  function showToast(message, type = "info") {
    const toast = document.getElementById("toast");
    if (!toast) return;
    toast.className = `toast ${type} show`;
    toast.textContent = message;
    setTimeout(() => {
      toast.classList.remove("show");
    }, 4000);
  }

  // ============================================================
  // EVENT BINDINGS
  // ============================================================
  function bindEvents() {
    document.getElementById("btnStart").addEventListener("click", async () => {
      const res = await api("/bot/start", { method: "POST" });
      showToast(res.success ? "Bot baslatildi" : res.error, res.success ? "success" : "error");
    });

    document.getElementById("btnStop").addEventListener("click", async () => {
      if (!confirm("Bot durdurulsun mu?")) return;
      const res = await api("/bot/stop", { method: "POST" });
      showToast(res.success ? "Bot durduruldu" : res.error, res.success ? "success" : "error");
    });

    document.getElementById("btnPause").addEventListener("click", async () => {
      const endpoint = state.botStatus === "paused" ? "/bot/resume" : "/bot/pause";
      const res = await api(endpoint, { method: "POST" });
      showToast(res.message || res.error, res.success ? "success" : "error");
    });

    // symbolSelect artik bir text input — hem "change" (datalist secimi/blur)
    // hem de "Enter" tuslanmasinda grafigi yeniden yukle.
    const symbolSelect = document.getElementById("symbolSelect");
    if (symbolSelect) {
      const applySymbol = () => {
        const val = (symbolSelect.value || "").trim().toUpperCase();
        if (!val || !val.includes("/")) return;
        const tf = document.getElementById("timeframeSelect").value;
        ChartManager.loadData(val, tf);
      };
      symbolSelect.addEventListener("change", applySymbol);
      symbolSelect.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          applySymbol();
        }
      });
    }

    document.getElementById("timeframeSelect").addEventListener("change", (e) => {
      const symEl = document.getElementById("symbolSelect");
      const sym = (symEl.value || "BTC/USDT").trim().toUpperCase();
      ChartManager.loadData(sym, e.target.value);
    });

    document.getElementById("btnClearLogs").addEventListener("click", () => {
      const c = document.getElementById("logsContainer");
      if (c) c.innerHTML = "";
    });

    const btnBacktest = document.getElementById("btnRunBacktest");
    if (btnBacktest) btnBacktest.addEventListener("click", runBacktest);

    const btnClearBt = document.getElementById("btnClearBacktest");
    if (btnClearBt) btnClearBt.addEventListener("click", () => {
      const el = document.getElementById("btResults");
      if (el) el.innerHTML = "";
      ChartManager.clearEquityPanel();
      ChartManager.clearBacktest();
    });

    // Tum stratejileri karsilastir
    const btnCompare = document.getElementById("btnCompareAll");
    if (btnCompare) btnCompare.addEventListener("click", runBacktestCompare);

    // Sinyal filtre checkbox'lari -> marker'lari yeniden yukle
    document.querySelectorAll(".signal-filter").forEach((cb) => {
      cb.addEventListener("change", () => {
        ChartManager.reloadSignalsWithFilter();
      });
    });

    // Alarm kur
    const btnAlarm = document.getElementById("btnSetAlarm");
    if (btnAlarm) btnAlarm.addEventListener("click", setAlarm);

    // Paper toggle
    const btnPaper = document.getElementById("btnPaperToggle");
    if (btnPaper) btnPaper.addEventListener("click", togglePaperMode);

    const btnPaperReset = document.getElementById("btnPaperReset");
    if (btnPaperReset) btnPaperReset.addEventListener("click", resetPaper);

    document.getElementById("manualOrderForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const symbol = document.getElementById("moSymbol").value;
      const side = document.querySelector('input[name="side"]:checked').value;
      const amount = parseFloat(document.getElementById("moAmount").value);
      const orderType = document.getElementById("moType").value;
      const priceRaw = document.getElementById("moPrice").value;
      const price = priceRaw ? parseFloat(priceRaw) : null;

      const body = { symbol, side, amount, order_type: orderType };
      if (price) body.price = price;

      const res = await api("/order/manual", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (res.success) {
        showToast("Emir gonderildi", "success");
        refreshTrades();
      } else {
        showToast(res.error || "Emir basarisiz", "error");
      }
    });
  }

  // ============================================================
  // BACKTEST
  // ============================================================
  async function runBacktest() {
    const symbol = document.getElementById("btSymbol")?.value?.trim().toUpperCase() || "BTC/USDT";
    const timeframe = document.getElementById("btTimeframe")?.value || "15m";
    const strategy = document.getElementById("btStrategy")?.value || "rsi_bb";
    const balance = parseFloat(document.getElementById("btBalance")?.value || "1000");
    const limit = parseInt(document.getElementById("btLimit")?.value || "500");
    const slPct = parseFloat(document.getElementById("btStopLoss")?.value || "2") / 100;
    const tpPct = parseFloat(document.getElementById("btTakeProfit")?.value || "4") / 100;
    const posPct = parseFloat(document.getElementById("btPositionSize")?.value || "5") / 100;
    const demoMode = document.getElementById("btDemoMode")?.checked || false;

    const btn = document.getElementById("btnRunBacktest");
    if (btn) { btn.disabled = true; btn.textContent = "Calisiyor..."; }

    const res = await api("/backtest", {
      method: "POST",
      body: JSON.stringify({
        symbol,
        timeframe,
        strategy,
        initial_balance: balance,
        limit,
        stop_loss_pct: slPct,
        take_profit_pct: tpPct,
        position_size_pct: posPct,
        demo_mode: demoMode,
      }),
    });

    if (btn) { btn.disabled = false; btn.textContent = "Backtest Baslat"; }

    if (!res.success) {
      showToast(res.error || "Backtest basarisiz", "error");
      return;
    }

    const d = res.data;
    renderBacktestResults(d);

    // Grafik uzerinde sinyal ok'larini goster
    if (d.signals && d.signals.length) {
      ChartManager.loadData(symbol, timeframe).then(() => {
        ChartManager.showBacktestSignals(d.signals);
      });
    }

    addLog("INFO", `Backtest tamam: ${d.total_trades} trade, Return: ${d.total_return_pct?.toFixed(2)}%, Win: ${d.win_rate}%`);
  }

  function renderBacktestResults(d) {
    const container = document.getElementById("btResults");
    if (!container) return;

    const sign = (v) => (v >= 0 ? "+" : "");
    const cls = (v) => (v > 0 ? "positive" : v < 0 ? "negative" : "neutral");
    const fmt = (v, dec = 2) => (v == null ? "N/A" : Number(v).toFixed(dec));

    const metrics = [
      { label: "Net Kar/Zarar", val: `${sign(d.total_return_pct)}${fmt(d.total_return_pct)}%`, cls: cls(d.total_return_pct) },
      { label: "Final Bakiye", val: `$${fmt(d.final_balance)}`, cls: "neutral" },
      { label: "Toplam Trade", val: d.total_trades, cls: "neutral" },
      { label: "Win Rate", val: `${fmt(d.win_rate)}%`, cls: d.win_rate >= 50 ? "positive" : "negative" },
      { label: "Profit Factor", val: d.profit_factor != null ? fmt(d.profit_factor) : "∞", cls: d.profit_factor == null || d.profit_factor >= 1 ? "positive" : "negative" },
      { label: "Sharpe", val: fmt(d.sharpe_ratio, 3), cls: cls(d.sharpe_ratio) },
      { label: "Sortino", val: fmt(d.sortino_ratio, 3), cls: cls(d.sortino_ratio) },
      { label: "Calmar", val: d.calmar_ratio != null ? fmt(d.calmar_ratio, 3) : "N/A", cls: d.calmar_ratio == null ? "neutral" : cls(d.calmar_ratio) },
      { label: "Max Drawdown", val: `${fmt(d.max_drawdown_pct)}%`, cls: d.max_drawdown_pct > 10 ? "negative" : "neutral" },
      { label: "Recovery Factor", val: d.recovery_factor != null ? fmt(d.recovery_factor, 3) : "N/A", cls: "neutral" },
      { label: "Ort. Kazanc", val: `$${fmt(d.avg_win)}`, cls: "positive" },
      { label: "Ort. Kayip", val: `$${fmt(d.avg_loss)}`, cls: "negative" },
      { label: "Max Seri Kayip", val: d.max_consecutive_losses, cls: d.max_consecutive_losses >= 5 ? "negative" : "neutral" },
      { label: "Ort. Sure (bar)", val: fmt(d.avg_trade_duration_bars, 1), cls: "neutral" },
      { label: "Mum Sayisi", val: d.candle_count, cls: "neutral" },
      { label: "Demo Mod", val: d.demo_mode ? "EVET" : "HAYIR", cls: d.demo_mode ? "positive" : "neutral" },
    ];

    container.innerHTML = `<div class="bt-metric-grid">${metrics
      .map(
        (m) =>
          `<div class="bt-metric-card ${m.cls}"><div class="metric-label">${m.label}</div><div class="metric-val">${m.val}</div></div>`
      )
      .join("")}</div>`;

    // Equity curve -> ayri panele
    if (d.equity_curve && d.equity_curve.length > 1) {
      ChartManager.showEquityCurvePanel(d.equity_curve);
    }

    // Trade tablosu
    if (d.trades && d.trades.length > 0) {
      renderBacktestTradeTable(d.trades);
    }
  }

  function renderBacktestTradeTable(trades) {
    const container = document.getElementById("btTradeTable");
    if (!container) return;

    // Yalnizca cikis (sell) kayitlarini goster
    const exits = trades.filter((t) => t.side === "sell" || t.reason);
    if (!exits.length) {
      container.style.display = "none";
      return;
    }

    const rows = exits
      .slice(0, 50)
      .map((t) => {
        const pnl = Number(t.pnl || 0);
        const win = pnl > 0;
        const pct = t.pnl_pct != null ? Number(t.pnl_pct).toFixed(2) : "0.00";
        const pnlStr = pnl.toFixed(3);
        const dur = t.duration_bars || 0;
        const ts = t.exit_ts ? String(t.exit_ts).substring(0, 16).replace("T", " ") : (t.timestamp ? String(t.timestamp).substring(0, 16).replace("T", " ") : "");
        return `<tr class="${win ? "win" : "loss"}">
          <td>${ts}</td>
          <td>$${Number(t.entry_price || 0).toFixed(2)}</td>
          <td>$${Number(t.exit_price || t.price || 0).toFixed(2)}</td>
          <td>${pct}%</td>
          <td>${pnl >= 0 ? "+" : ""}${pnlStr}</td>
          <td>${dur}</td>
          <td>${t.reason || ""}</td>
        </tr>`;
      })
      .join("");

    container.innerHTML = `
      <table class="bt-trade-table">
        <thead><tr><th>Tarih</th><th>Giris</th><th>Cikis</th><th>%</th><th>PnL</th><th>Bar</th><th>Sebep</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    container.style.display = "block";
  }

  // ============================================================
  // BACKTEST COMPARE (tum stratejileri ayni veri uzerinde kosar)
  // ============================================================
  async function runBacktestCompare() {
    const symbol = document.getElementById("btSymbol")?.value?.trim().toUpperCase() || "BTC/USDT";
    const timeframe = document.getElementById("btTimeframe")?.value || "15m";
    const balance = parseFloat(document.getElementById("btBalance")?.value || "1000");
    const limit = parseInt(document.getElementById("btLimit")?.value || "500");
    const slPct = parseFloat(document.getElementById("btStopLoss")?.value || "2") / 100;
    const tpPct = parseFloat(document.getElementById("btTakeProfit")?.value || "4") / 100;
    const posPct = parseFloat(document.getElementById("btPositionSize")?.value || "5") / 100;

    const btn = document.getElementById("btnCompareAll");
    if (btn) { btn.disabled = true; btn.textContent = "Karsilastiriliyor..."; }

    const res = await api("/backtest/compare", {
      method: "POST",
      body: JSON.stringify({
        symbol,
        timeframe,
        balance,
        limit,
        stop_loss_pct: slPct,
        take_profit_pct: tpPct,
        position_size_pct: posPct,
        strategies: ["rsi_bb", "ma_crossover", "macd_signal", "stoch_rsi"],
      }),
    });

    if (btn) { btn.disabled = false; btn.textContent = "Tum Stratejileri Karsilastir"; }

    if (!res || (!res.success && !res.results)) {
      showToast(res?.error || res?.detail || "Karsilastirma hatasi", "error");
      return;
    }

    renderCompareTable(res.results);
    addLog("INFO", `Karsilastirma tamam: ${Object.keys(res.results).length} strateji`);
  }

  function renderCompareTable(results) {
    const container = document.getElementById("btResults");
    if (!container) return;

    const strategies = Object.keys(results);
    const metrics = ["total_return_pct", "win_rate", "profit_factor", "sharpe_ratio", "max_drawdown_pct", "total_trades"];
    const labels = ["Net Kar %", "Win Rate %", "Profit Factor", "Sharpe", "Max DD %", "Trade Sayisi"];

    // En iyi degerleri bul (max_drawdown hic — digerleri cok iyi)
    const best = {};
    metrics.forEach((m) => {
      const vals = strategies
        .map((s) => results[s]?.[m])
        .filter((v) => v != null && !isNaN(v) && typeof v === "number");
      if (!vals.length) { best[m] = null; return; }
      best[m] = m === "max_drawdown_pct" ? Math.min(...vals) : Math.max(...vals);
    });

    const headerRow = `<tr><th>Metrik</th>${strategies.map((s) => `<th>${s}</th>`).join("")}</tr>`;
    const rows = metrics
      .map((m, i) => {
        const cells = strategies
          .map((s) => {
            const r = results[s];
            if (!r || r.error) return `<td style="color:var(--danger)">-</td>`;
            const v = r?.[m];
            const fmt = v == null ? "N/A" : typeof v === "number" ? Number(v).toFixed(2) : v;
            const isBest = v != null && best[m] != null && v == best[m];
            return `<td class="${isBest ? "best" : ""}">${fmt}</td>`;
          })
          .join("");
        return `<tr><td>${labels[i]}</td>${cells}</tr>`;
      })
      .join("");

    container.innerHTML = `<table class="compare-table"><thead>${headerRow}</thead><tbody>${rows}</tbody></table>`;
  }

  // ============================================================
  // FIYAT ALARMLARI
  // ============================================================
  async function loadAlarms() {
    const res = await api("/alarms");
    const list = document.getElementById("alarmsList");
    if (!list) return;
    const alarms = (res && res.data) || [];
    if (!alarms.length) {
      list.innerHTML = '<div class="empty">Alarm yok</div>';
      return;
    }
    list.innerHTML = alarms
      .map((a) => {
        const arrow = a.direction === "above" ? "≥" : "≤";
        return `
        <div class="alarm-item" style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--border,#333);">
          <span>${a.symbol} ${arrow} ${a.price}</span>
          <button class="btn btn-small" data-alarm-id="${a.id}" style="font-size:10px;padding:2px 6px;">Sil</button>
        </div>`;
      })
      .join("");
    list.querySelectorAll("[data-alarm-id]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.alarmId;
        await api(`/alarms/${id}`, { method: "DELETE" });
        loadAlarms();
      });
    });
  }

  async function setAlarm() {
    const symbol = document.getElementById("alarmSymbol").value;
    const priceRaw = document.getElementById("alarmPrice").value;
    const direction = document.getElementById("alarmDir").value;
    const price = parseFloat(priceRaw);
    if (!price || price <= 0) {
      showToast("Gecerli fiyat girin", "error");
      return;
    }
    const res = await api("/alarms", {
      method: "POST",
      body: JSON.stringify({ symbol, price, direction }),
    });
    if (res.success) {
      showToast(`Alarm kuruldu: ${symbol} ${direction} ${price}`, "success");
      document.getElementById("alarmPrice").value = "";
      loadAlarms();
    } else {
      showToast(res.error || "Alarm kurulamadi", "error");
    }
  }

  // ============================================================
  // PAPER TRADING
  // ============================================================
  let paperEnabled = false;

  async function refreshPaperStatus() {
    const res = await api("/paper/status");
    if (!res.success) return;
    const d = res.data;
    paperEnabled = !!d.enabled;

    const label = document.getElementById("paperLabel");
    const btn = document.getElementById("btnPaperToggle");
    const card = document.getElementById("paperCard");
    if (label) {
      label.textContent = `PAPER: ${paperEnabled ? "ON" : "OFF"}`;
      label.style.color = paperEnabled ? "var(--success)" : "var(--text-secondary)";
    }
    if (btn) btn.textContent = paperEnabled ? "Kapat" : "Ac";
    if (card) card.style.display = paperEnabled ? "block" : "none";

    if (paperEnabled) {
      const bal = document.getElementById("paperBalance");
      const eq = document.getElementById("paperEquity");
      const pnl = document.getElementById("paperPnl");
      const pos = document.getElementById("paperPositions");
      if (bal) bal.textContent = (d.paper_balance || 0).toFixed(2);
      if (eq) eq.textContent = (d.equity || 0).toFixed(2);
      if (pnl) {
        const v = d.total_pnl || 0;
        pnl.textContent = (v >= 0 ? "+" : "") + v.toFixed(4);
        pnl.style.color = v >= 0 ? "var(--success)" : "var(--danger)";
      }
      if (pos) pos.textContent = d.open_positions_count || 0;
    }
  }

  async function togglePaperMode() {
    const endpoint = paperEnabled ? "/paper/stop" : "/paper/start";
    const res = await api(endpoint, { method: "POST" });
    if (res.success) {
      showToast(res.message || "Paper mode degistirildi", "success");
      refreshPaperStatus();
    } else {
      showToast(res.error || "Islem basarisiz", "error");
    }
  }

  async function resetPaper() {
    if (!confirm("Paper bakiye sifirlansin mi? Tum paper pozisyon ve geçmiş silinecek.")) return;
    const res = await api("/paper/reset", {
      method: "POST",
      body: JSON.stringify({}),
    });
    if (res.success) {
      showToast("Paper sifirlandi", "success");
      refreshPaperStatus();
    }
  }

  // ============================================================
  // INIT
  // ============================================================
  async function init() {
    getApiKey(); // localStorage'da yoksa hemen sor, charts.js fetch'inden önce
    ChartManager.init();
    ChartManager.loadData("BTC/USDT", "15m");
    bindEvents();
    connectWs();

    // OKX spot pariteleri datalist'lere doldur
    loadMarkets();

    // Ilk veri yukle
    refreshStrategies();
    refreshTrades();
    refreshPnl();
    refreshRisk();
    loadAlarms();
    refreshPaperStatus();

    // Periyodik refresh
    setInterval(() => {
      refreshTrades();
      refreshPnl();
      refreshRisk();
      loadAlarms();
      refreshPaperStatus();
    }, 15000);

    addLog("INFO", "Dashboard baslatildi");
  }

  return { init };
})();

document.addEventListener("DOMContentLoaded", App.init);
