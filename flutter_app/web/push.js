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

// Register the dedicated push service worker (Flutter built with --pwa-strategy=none).
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch(console.error);
}
