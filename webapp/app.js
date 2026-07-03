const fmt = (v, d = 2) =>
  v === null || v === undefined ? "n/a" : Number(v).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (v, d = 2) => (v === null || v === undefined ? "n/a" : (v >= 0 ? "+" : "") + fmt(v, d));
const cls = (v) => (Number(v) >= 0 ? "green" : "red");

async function loadOverview() {
  const res = await fetch("/api/overview");
  if (!res.ok) throw new Error("API error");
  const data = await res.json();
  renderPortfolio(data.portfolio);
  renderMarket(data.market);
  document.getElementById("as-of").textContent =
    "Mis à jour : " + new Date(data.as_of).toLocaleString("fr-FR");
}

function renderPortfolio(p) {
  const summary = document.getElementById("portfolio-summary");
  if (!p.holdings.length) {
    summary.innerHTML = `<p class="muted">Aucune position. Ajoutez vos actions dans <code>config/portfolio.json</code>.</p>`;
    document.getElementById("holdings").innerHTML = "";
    return;
  }
  summary.innerHTML = `
    <div class="row between">
      <div><div class="muted small">Valeur</div><div class="big">${fmt(p.total_value, 0)} MAD</div></div>
      <div style="text-align:right">
        <div class="muted small">P/L net (frais ${fmt(p.fee_rate * 100, 2)}%)</div>
        <div class="big ${cls(p.total_net_pl)}">${signed(p.total_net_pl, 0)}</div>
        <div class="${cls(p.total_pl_pct)}">${signed(p.total_pl_pct, 1)}%</div>
      </div>
    </div>`;

  const holdings = [...p.holdings].sort((a, b) => (a.advice === "SELL" ? -1 : 1) - (b.advice === "SELL" ? -1 : 1));
  document.getElementById("holdings").innerHTML = holdings
    .map((h) => {
      const badge = h.advice === "SELL" ? `<span class="badge sell">VENDRE</span>` : `<span class="badge hold">CONSERVER</span>`;
      return `
      <div class="card holding">
        <div class="head">
          <div><span class="sym">${h.symbol}</span> <span class="name">${h.company_name}</span></div>
          ${badge}
        </div>
        <div class="meta">
          <span>${fmt(h.quantity, 0)} × ${fmt(h.current_price)} = ${fmt(h.market_value, 0)} MAD</span>
          <span class="${cls(h.net_pl)}">${signed(h.net_pl, 0)} (${signed(h.net_pl_pct, 1)}%)</span>
        </div>
        <div class="reason">Acheté @ ${fmt(h.buy_price)} — ${h.advice_reason}</div>
      </div>`;
    })
    .join("");
}

function moversHtml(list) {
  if (!list.length) return `<p class="muted small">—</p>`;
  return list
    .map(
      (m) =>
        `<div class="line"><span>${m.symbol}</span><span class="${cls(m.daily_variation)}">${signed(m.daily_variation)}%</span></div>`
    )
    .join("");
}

function renderMarket(m) {
  document.getElementById("gainers").innerHTML = `<h3>Hausses</h3>${moversHtml(m.gainers)}`;
  document.getElementById("losers").innerHTML = `<h3>Baisses</h3>${moversHtml(m.losers)}`;
  const opp = m.opportunities.length
    ? m.opportunities.map((o) => `<div class="line"><span>${o.symbol}</span><span>${Math.round(o.buy_score)}/100</span></div>`).join("")
    : `<p class="muted small">Pas encore assez d'historique pour des scores discriminants.</p>`;
  document.getElementById("opportunities").innerHTML = `<h3>Opportunités</h3>${opp}`;
}

// ---- Web Push ----
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

async function enableNotifications() {
  const status = document.getElementById("notif-status");
  try {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") {
      status.textContent = "Notifications refusées par le navigateur.";
      return;
    }
    const reg = await navigator.serviceWorker.ready;
    const keyRes = await fetch("/api/vapid-public-key");
    const { key } = await keyRes.json();
    if (!key) {
      status.textContent = "Clé serveur (VAPID) manquante côté serveur.";
      return;
    }
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    });
    await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub),
    });
    status.textContent = "✅ Notifications activées sur cet appareil.";
  } catch (e) {
    status.textContent = "Erreur d'activation : " + e.message;
  }
}

async function testNotification() {
  const res = await fetch("/api/push/test", { method: "POST" });
  const { sent } = await res.json();
  document.getElementById("notif-status").textContent =
    sent > 0 ? `Test envoyé à ${sent} appareil(s).` : "Aucun appareil abonné (activez d'abord).";
}

async function runNow() {
  const btn = document.getElementById("run-now");
  const status = document.getElementById("notif-status");
  btn.disabled = true;
  status.textContent = "⏳ Collecte des cours en cours… (~30 s)";
  try {
    const res = await fetch("/api/run-now", { method: "POST" });
    if (!res.ok) throw new Error("API " + res.status);
    // The run is async on the server; give it time, then refresh the view.
    setTimeout(async () => {
      await loadOverview().catch(console.error);
      status.textContent = "✅ Données actualisées. Notification envoyée si abonné.";
      btn.disabled = false;
    }, 33000);
  } catch (e) {
    status.textContent = "Erreur : " + e.message;
    btn.disabled = false;
  }
}

document.getElementById("refresh").addEventListener("click", () => loadOverview().catch(console.error));
document.getElementById("enable-notif").addEventListener("click", enableNotifications);
document.getElementById("test-notif").addEventListener("click", testNotification);
document.getElementById("run-now").addEventListener("click", runNow);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch(console.error);
}

loadOverview().catch((e) => {
  document.getElementById("portfolio-summary").innerHTML = `<p class="red">Erreur de chargement : ${e.message}</p>`;
});
