/* Runs at document_start on ApplyRight web app tabs only.
   Sets data-autoapply-ext and answers profile "Check again" postMessage probes. */
(function () {
  const APP_HOSTS = [
    "autoapplynow.in",
    "mango-ocean-0f1de6810.2.azurestaticapps.net",
    "localhost",
  ];
  const host = location.hostname || "";
  const isApp =
    APP_HOSTS.some((h) => host === h || host.endsWith("." + h)) ||
    document.querySelector('meta[name="autoapply-app"]');
  if (!isApp) return;

  try {
    document.documentElement.setAttribute("data-autoapply-ext", "installed");
  } catch (_) {}

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.type === "AUTOAPPLY_CHECK_EXTENSION") {
      window.postMessage({ type: "AUTOAPPLY_EXTENSION_STATUS", installed: true }, "*");
    }
    if (data.type === "AUTOAPPLY_SYNC_TOKEN" && data.token) {
      try {
        chrome.storage.local.set({ autoapply_token: data.token }, () => {
          document.documentElement.setAttribute("data-autoapply-ext", "connected");
          window.postMessage({ type: "AUTOAPPLY_TOKEN_SYNCED", ok: true }, "*");
        });
      } catch (_) {}
    }
  });
})();
