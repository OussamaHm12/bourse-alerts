// ------------------------------- helpers ---------------------------------- //
const fmt = (v, d = 2) =>
  v === null || v === undefined ? "n/a" : Number(v).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (v, d = 2) => (v === null || v === undefined ? "n/a" : (v >= 0 ? "+" : "") + fmt(v, d));
const cls = (v) => (Number(v) >= 0 ? "green" : "red");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const el = (id) => document.getElementById(id);
async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error("API " + res.status);
  return res.json();
}
const labelClass = (label) =>
  ({ ACHETER: "buy", SURVEILLER: "watch", ÉVITER: "avoid", NEUTRE: "neutral" }[label] || "neutral");
const badge = (label) => `<span class="badge ${labelClass(label)}">${esc(label)}</span>`;

// ------------------------------- router ----------------------------------- //
const loaders = {}; // view id -> loader function
const loaded = {};

function showView(id) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === id));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === id));
  window.scrollTo(0, 0);
  if (loaders[id] && !loaded[id]) {
    loaded[id] = true;
    loaders[id]().catch(console.error);
  }
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));

// ---------------------------- PORTFOLIO view ------------------------------ //
async function loadOverview() {
  const data = await getJSON("/api/overview");
  renderPortfolio(data.portfolio);
  el("as-of").textContent = "Mis à jour : " + new Date(data.as_of).toLocaleString("fr-FR");
}

function renderPortfolio(p) {
  const summary = el("portfolio-summary");
  if (!p.holdings.length) {
    summary.innerHTML = `<p class="muted">Aucune position. Renseignez <code>PORTFOLIO_JSON</code> côté serveur.</p>`;
    el("holdings").innerHTML = "";
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
  el("holdings").innerHTML = holdings
    .map((h) => {
      const b = h.advice === "SELL" ? `<span class="badge avoid">VENDRE</span>` : `<span class="badge buy">CONSERVER</span>`;
      return `
      <div class="card holding tap" data-sym="${esc(h.symbol)}">
        <div class="head"><div><span class="sym">${esc(h.symbol)}</span> <span class="name">${esc(h.company_name)}</span></div>${b}</div>
        <div class="meta">
          <span>${fmt(h.quantity, 0)} × ${fmt(h.current_price)} = ${fmt(h.market_value, 0)} MAD</span>
          <span class="${cls(h.net_pl)}">${signed(h.net_pl, 0)} (${signed(h.net_pl_pct, 1)}%)</span>
        </div>
        <div class="reason">Acheté @ ${fmt(h.buy_price)} — ${esc(h.advice_reason)}</div>
      </div>`;
    })
    .join("");
}

// ------------------------------ MARKET view ------------------------------- //
const market = { sort: "score", q: "", sectors: [], sector: null };

async function loadMarket() {
  const params = new URLSearchParams({ sort: market.sort });
  if (market.q) params.set("q", market.q);
  if (market.sector) params.set("sector", market.sector);
  const data = await getJSON("/api/stocks?" + params.toString());
  if (!market.sectors.length && data.sectors) {
    market.sectors = data.sectors;
    renderSectorChips();
  }
  renderStockList(data.stocks);
}

function renderSectorChips() {
  el("sectors-strip").innerHTML =
    `<button class="chip ${market.sector ? "" : "active"}" data-sector="">Tous</button>` +
    market.sectors
      .map((s) => `<button class="chip ${market.sector === s ? "active" : ""}" data-sector="${esc(s)}">${esc(s)}</button>`)
      .join("");
  el("sectors-strip")
    .querySelectorAll(".chip")
    .forEach((c) =>
      c.addEventListener("click", () => {
        market.sector = c.dataset.sector || null;
        el("sectors-strip").querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));
        c.classList.add("active");
        loadMarket().catch(console.error);
      })
    );
}

function renderStockList(stocks) {
  if (!stocks.length) {
    el("market-list").innerHTML = `<p class="muted">Aucune action.</p>`;
    return;
  }
  el("market-list").innerHTML = stocks
    .map((s) => {
      const arrow = s.trend === "haussier" ? "▲" : s.trend === "baissier" ? "▼" : "•";
      const tclass = s.trend === "haussier" ? "green" : s.trend === "baissier" ? "red" : "muted";
      return `
      <div class="card rowitem tap" data-sym="${esc(s.symbol)}">
        <div class="ri-left">
          <div><span class="sym">${esc(s.symbol)}</span> <span class="tr ${tclass}">${arrow}</span></div>
          <div class="name">${esc(s.company_name)}</div>
        </div>
        <div class="ri-mid">
          <div>${fmt(s.price)} <span class="muted small">MAD</span></div>
          <div class="${cls(s.daily_variation)} small">${signed(s.daily_variation)}%</div>
        </div>
        <div class="ri-right">
          ${badge(s.label)}
          <div class="muted small">score ${s.buy_score === null ? "n/a" : Math.round(s.buy_score)}</div>
        </div>
      </div>`;
    })
    .join("");
}

let searchTimer;
el("market-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  market.q = e.target.value.trim();
  searchTimer = setTimeout(() => loadMarket().catch(console.error), 250);
});
el("market-sort").addEventListener("change", (e) => {
  market.sort = e.target.value;
  loadMarket().catch(console.error);
});

// --------------------------- OPPORTUNITIES view --------------------------- //
const opps = { min: 0 };

async function loadOpps() {
  const data = await getJSON("/api/opportunities?min_score=" + opps.min);
  const list = el("opp-list");
  if (!data.opportunities.length) {
    list.innerHTML = `<p class="muted">Aucune opportunité au-dessus de ${opps.min}. L'historique s'enrichit avec le temps.</p>`;
    return;
  }
  list.innerHTML = data.opportunities
    .map((o) => {
      const reasons = (o.reasons || []).slice(0, 2).map((r) => `<li>${esc(r)}</li>`).join("");
      return `
      <div class="card opp tap" data-sym="${esc(o.symbol)}">
        <div class="row between">
          <div><span class="sym">${esc(o.symbol)}</span> <span class="name">${esc(o.company_name)}</span></div>
          <div class="scorepill">${Math.round(o.buy_score)}<span>/100</span></div>
        </div>
        <div class="row between small">
          <span>${badge(o.label)}</span>
          <span class="${cls(o.daily_variation)}">${signed(o.daily_variation)}%</span>
        </div>
        <ul class="reasons">${reasons}</ul>
      </div>`;
    })
    .join("");
}

el("opp-filters")
  .querySelectorAll(".chip")
  .forEach((c) =>
    c.addEventListener("click", () => {
      opps.min = Number(c.dataset.min);
      el("opp-filters").querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      loadOpps().catch(console.error);
    })
  );

// ------------------------------- NEWS view -------------------------------- //
async function loadNews() {
  const data = await getJSON("/api/news");
  const list = el("news-list");
  if (!data.news.length) {
    list.innerHTML = `<p class="muted">Aucune actualité collectée pour le moment.</p>`;
    return;
  }
  list.innerHTML = data.news
    .map((n) => {
      const when = n.published_at ? new Date(n.published_at).toLocaleDateString("fr-FR") : "";
      const sent = n.sentiment ? `<span class="tag ${esc(n.sentiment)}">${esc(n.sentiment)}</span>` : "";
      const sym = n.symbol ? `<span class="tag sym">${esc(n.symbol)}</span>` : "";
      return `
      <a class="card news" href="${esc(n.url)}" target="_blank" rel="noopener">
        <div class="news-title">${esc(n.title)}</div>
        <div class="news-meta muted small">${esc(n.source)} · ${when} ${sym} ${sent}</div>
      </a>`;
    })
    .join("");
}

// ------------------------------ DETAIL sheet ------------------------------ //
function sparkline(history) {
  const pts = (history || []).filter((h) => h.p != null);
  if (pts.length < 2) return `<div class="muted small">Pas encore assez d'historique pour un graphique.</div>`;
  const W = 320, H = 80, pad = 4;
  const ys = pts.map((p) => p.p);
  const min = Math.min(...ys), max = Math.max(...ys), span = max - min || 1;
  const x = (i) => pad + (i * (W - 2 * pad)) / (pts.length - 1);
  const y = (v) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.p).toFixed(1)}`).join(" ");
  const up = pts[pts.length - 1].p >= pts[0].p;
  const color = up ? "var(--green)" : "var(--red)";
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2" />
  </svg>`;
}

const COMPONENT_LABELS = {
  momentum: "Momentum",
  volume_anomaly: "Volume",
  valuation_opportunity: "Valorisation",
  support_proximity: "Support",
  sector_strength: "Secteur",
  news_sentiment: "Actus",
};

function scoreBars(components) {
  return Object.entries(components || {})
    .map(([k, v]) => {
      const pct = Math.max(0, Math.min(100, v));
      return `<div class="bar-row"><span>${COMPONENT_LABELS[k] || k}</span>
        <div class="bar"><div style="width:${pct}%"></div></div><b>${Math.round(v)}</b></div>`;
    })
    .join("");
}

function metricRow(label, value, suffix = "") {
  return `<div class="mrow"><span>${label}</span><b>${value === null || value === undefined ? "n/a" : fmt(value) + suffix}</b></div>`;
}

function openDetail(symbol) {
  el("detail").classList.add("open");
  el("detail-title").innerHTML = `<span class="sym">${esc(symbol)}</span>`;
  el("detail-body").innerHTML = `<p class="muted">Chargement…</p>`;
  getJSON("/api/stock/" + encodeURIComponent(symbol))
    .then(renderDetail)
    .catch((e) => (el("detail-body").innerHTML = `<p class="red">Erreur : ${esc(e.message)}</p>`));
}

function renderDetail(d) {
  const s = d.score;
  el("detail-title").innerHTML = `<span class="sym">${esc(d.symbol)}</span> <span class="name">${esc(d.company_name)}</span>`;
  const reasons = s ? s.reasons.map((r) => `<li>${esc(r)}</li>`).join("") : "";
  const risks = s ? s.risks.map((r) => `<li>${esc(r)}</li>`).join("") : "";
  const news = (d.news || [])
    .map((n) => `<a class="news-line" href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>`)
    .join("");
  el("detail-body").innerHTML = `
    <div class="d-head">
      <div><div class="big">${fmt(d.price)} <span class="muted small">MAD</span></div>
        <div class="${cls(d.daily_variation)}">${signed(d.daily_variation)}% aujourd'hui</div></div>
      <div style="text-align:right">${s ? badge(s.label) : ""}
        <div class="muted small">${esc(d.sector || "")}</div></div>
    </div>
    <div class="card">${sparkline(d.history)}</div>
    ${
      s
        ? `<h3>Score d'opportunité</h3>
    <div class="card">
      <div class="score3">
        <div><span class="muted small">Acheter</span><div class="big green">${Math.round(s.buy)}</div></div>
        <div><span class="muted small">Surveiller</span><div class="big">${Math.round(s.watch)}</div></div>
        <div><span class="muted small">Éviter</span><div class="big red">${Math.round(s.avoid)}</div></div>
      </div>
      <div class="bars">${scoreBars(s.components)}</div>
    </div>
    <div class="grid">
      <div class="card"><h3>Atouts</h3><ul class="reasons">${reasons}</ul></div>
      <div class="card"><h3>Risques</h3><ul class="risks">${risks}</ul></div>
    </div>`
        : ""
    }
    <h3>Indicateurs techniques</h3>
    <div class="card metrics">
      ${metricRow("Momentum 5j", d.momentum.d5, "%")}
      ${metricRow("Momentum 30j", d.momentum.d30, "%")}
      ${metricRow("Momentum 90j", d.momentum.d90, "%")}
      ${metricRow("MM20", d.moving_averages.ma20)}
      ${metricRow("MM50", d.moving_averages.ma50)}
      ${metricRow("MM200", d.moving_averages.ma200)}
      ${metricRow("Volatilité 30j", d.volatility_30d, "%")}
      ${metricRow("Volume vs moy.", d.volume_anomaly, "×")}
      ${metricRow("Support", d.support)}
      ${metricRow("Résistance", d.resistance)}
      ${metricRow("+ haut 52 sem.", d.week52_high)}
      ${metricRow("+ bas 52 sem.", d.week52_low)}
    </div>
    ${news ? `<h3>Actualités liées</h3><div class="card">${news}</div>` : ""}`;
}

function closeDetail() {
  el("detail").classList.remove("open");
}
el("detail-back").addEventListener("click", closeDetail);

// delegate taps on any element carrying data-sym
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-sym]");
  if (t) openDetail(t.dataset.sym);
});

// ---------------------------- notifications ------------------------------- //
async function enableNotifications() {
  const status = el("notif-status");
  try {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return void (status.textContent = "Notifications refusées par le navigateur.");
    const reg = await navigator.serviceWorker.ready;
    const { key } = await getJSON("/api/vapid-public-key");
    if (!key) return void (status.textContent = "Clé serveur (VAPID) manquante côté serveur.");
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64(key) });
    await fetch("/api/push/subscribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sub) });
    status.textContent = "✅ Notifications activées sur cet appareil.";
  } catch (e) {
    status.textContent = "Erreur d'activation : " + e.message;
  }
}
function urlB64(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}
async function testNotification() {
  const { sent } = await (await fetch("/api/push/test", { method: "POST" })).json();
  el("notif-status").textContent = sent > 0 ? `Test envoyé à ${sent} appareil(s).` : "Aucun appareil abonné (activez d'abord).";
}
async function runNow() {
  const btn = el("run-now"), status = el("notif-status");
  btn.disabled = true;
  status.textContent = "⏳ Collecte des cours en cours… (~30 s)";
  try {
    const res = await fetch("/api/run-now", { method: "POST" });
    if (!res.ok) throw new Error("API " + res.status);
    setTimeout(async () => {
      await refreshAll();
      status.textContent = "✅ Données actualisées. Notification envoyée si abonné.";
      btn.disabled = false;
    }, 33000);
  } catch (e) {
    status.textContent = "Erreur : " + e.message;
    btn.disabled = false;
  }
}

// ------------------------------ wiring ------------------------------------ //
loaders["view-portfolio"] = loadOverview;
loaders["view-market"] = loadMarket;
loaders["view-opps"] = loadOpps;
loaders["view-news"] = loadNews;

async function refreshAll() {
  loaded["view-market"] = loaded["view-opps"] = loaded["view-news"] = false;
  await loadOverview().catch(console.error);
  const active = document.querySelector(".view.active");
  if (active && loaders[active.id]) {
    loaded[active.id] = true;
    loaders[active.id]().catch(console.error);
  }
}

el("refresh").addEventListener("click", () => refreshAll().catch(console.error));
el("enable-notif").addEventListener("click", enableNotifications);
el("test-notif").addEventListener("click", testNotification);
el("run-now").addEventListener("click", runNow);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch(console.error);
  let refreshing = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (refreshing) return;
    refreshing = true;
    location.reload();
  });
}

// initial load: portfolio is the default active view
loaded["view-portfolio"] = true;
loadOverview().catch((e) => {
  el("portfolio-summary").innerHTML = `<p class="red">Erreur de chargement : ${esc(e.message)}</p>`;
});
