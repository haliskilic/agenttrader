/*
 * AgentTrader - Telegram Widget (opsiyonel)
 * Telegram entegrasyon durumu için gösterge.
 */

const TelegramWidget = (() => {
  async function checkStatus() {
    try {
      const res = await fetch("/api/status");
      const json = await res.json();
      if (json.success) {
        const el = document.getElementById("telegramStatus");
        if (el) {
          // Telegram bağlı mı? Server tarafından belirlenir, basit gösterge
          const tgText = el.querySelector(".tg-text");
          if (tgText) {
            tgText.textContent = "Aktif";
            el.classList.add("connected");
          }
        }
      }
    } catch (e) {
      console.error("TG status hatasi", e);
    }
  }

  function init() {
    checkStatus();
    setInterval(checkStatus, 30000);
  }

  return { init };
})();

document.addEventListener("DOMContentLoaded", TelegramWidget.init);
