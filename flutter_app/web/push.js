// Proven web-push logic, exposed on window for the Flutter/Dart UI to call.
function _urlB64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

window.appEnablePush = async function () {
  try {
    if (!("serviceWorker" in navigator)) return "Service worker non supporté sur ce navigateur.";
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return "Notifications refusées par le navigateur.";
    const reg = await navigator.serviceWorker.ready;
    const res = await fetch("/api/vapid-public-key");
    const { key } = await res.json();
    if (!key) return "Clé serveur (VAPID) manquante côté serveur.";
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: _urlB64ToUint8Array(key),
    });
    await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub),
    });
    return "✅ Notifications activées sur cet appareil.";
  } catch (e) {
    return "Erreur d'activation : " + e.message;
  }
};

window.appTestPush = async function () {
  try {
    const res = await fetch("/api/push/test", { method: "POST" });
    const { sent } = await res.json();
    return sent > 0 ? "Test envoyé à " + sent + " appareil(s)." : "Aucun appareil abonné (activez d'abord).";
  } catch (e) {
    return "Erreur : " + e.message;
  }
};

window.appRunNow = async function () {
  try {
    const res = await fetch("/api/run-now", { method: "POST" });
    if (!res.ok) throw new Error("API " + res.status);
    return "⏳ Collecte lancée…";
  } catch (e) {
    return "Erreur : " + e.message;
  }
};

// --------------------------------------------------------------------------- //
// Self-healing subscription                                                    //
// --------------------------------------------------------------------------- //
// The server can lose a device's subscription while the browser still holds a
// perfectly good one: `send_push_to_all` prunes on a 404/410 from the push
// service, and a restored backup or a fresh volume drops the table outright.
// Nothing repaired that, and nothing reported it either — sends then "succeed"
// with zero recipients, the in-app inbox keeps filling normally because
// `save_notification` runs first, and the only symptom is an absence. That is
// how the 2026-07-22 outage was found: a day late, by the owner noticing a
// morning digest that never arrived.
//
// So every load re-POSTs the current subscription. `save_subscription` matches on
// endpoint and updates in place, so an already-registered device costs one
// idempotent write and never counts against MAX_SUBSCRIPTIONS (locked by test —
// without that property this loop would fill the table and then start rejecting).
//
// THIS NEVER PROMPTS. It acts only where permission is ALREADY granted; a browser
// that was never asked, or that refused, is left alone. An unrequested permission
// dialog on every load is how an app loses that permission permanently.

// The push endpoints are private (services/auth.py: deny-by-default), and login
// happens *after* this script loads — so a first attempt legitimately lands
// unauthenticated. These delays cover the owner typing a password, then give up:
// a device that is genuinely signed out has nothing to heal.
const _RESYNC_DELAYS_MS = [0, 3000, 10000, 30000, 60000];
// A tab-switch is a fine moment to re-check, an expensive one to re-check every
// time. Success is good for this long before the app asks again.
const _RESYNC_MIN_INTERVAL_MS = 5 * 60 * 1000;

let _resyncRunning = false;
let _lastResyncOk = 0;

// Returns true when there is nothing left to do (healed, or nothing to heal),
// false when the attempt should be retried later.
async function _resyncSubscriptionOnce() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return true;
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return true;

  // Cheap and public, so an unauthenticated tab settles here instead of firing
  // 401s at the private endpoints below on every retry.
  const status = await fetch("/api/auth/status");
  if (!status.ok) return false;
  const { authenticated } = await status.json();
  if (!authenticated) return false;

  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    // Permission is granted but the browser dropped the subscription (it rotates
    // them on its own schedule). Re-subscribing is silent in this state.
    const res = await fetch("/api/vapid-public-key");
    if (!res.ok) return false;
    const { key } = await res.json();
    if (!key) return false;
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: _urlB64ToUint8Array(key),
    });
  }

  const post = await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sub),
  });
  return post.ok;
}

async function _resyncSubscription(reason) {
  if (_resyncRunning) return;
  if (Date.now() - _lastResyncOk < _RESYNC_MIN_INTERVAL_MS) return;
  _resyncRunning = true;
  try {
    for (const delay of _RESYNC_DELAYS_MS) {
      if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
      try {
        if (await _resyncSubscriptionOnce()) {
          _lastResyncOk = Date.now();
          return;
        }
      } catch (e) {
        // Never throw out of here: this is a background repair, and an unhandled
        // rejection on load is not worth breaking the app over.
        console.warn("push resync (" + reason + ") failed:", e);
      }
    }
  } finally {
    _resyncRunning = false;
  }
}

// Exposed so the Dart side can force a re-sync right after a successful login,
// which is the earliest moment this can possibly work.
window.appResyncPush = () => _resyncSubscription("manual");

// Register the dedicated push service worker (Flutter built with --pwa-strategy=none).
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch(console.error);
  _resyncSubscription("load");
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") _resyncSubscription("visible");
  });
}
