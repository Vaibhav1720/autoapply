/* background.js — AutoApply Chrome Extension service worker */

// Production backend (Azure Functions Consumption). The Flutter web app at
// mango-ocean-0f1de6810.2.azurestaticapps.net points to the same host, so a
// Google sign-in on either surface produces the same userId/profile/resume.
const DEFAULT_API_BASE = "https://autoapply-func-dev.azurewebsites.net";
const LEGACY_API_BASE = "https://autoapply-func-dev.azurewebsites.net";

async function getApiBase() {
  return new Promise((res) => {
    chrome.storage.local.get(["autoapply_api_base"], (r) => {
      const stored = r.autoapply_api_base;
      // Auto-migrate: if a previous install pinned the legacy backend, swap
      // it for the new shared one so the user doesn't have to fix Settings.
      if (stored === LEGACY_API_BASE) {
        chrome.storage.local.set({ autoapply_api_base: DEFAULT_API_BASE });
        return res(DEFAULT_API_BASE);
      }
      res(stored || DEFAULT_API_BASE);
    });
  });
}

/** Request optional host access for one origin (user gesture required). */
function ensureHostPermissionForUrl(url, sendResponse) {
  try {
    if (!url || !/^https?:\/\//i.test(url)) {
      sendResponse({ ok: false, error: "Not a web page" });
      return;
    }
    const origin = new URL(url).origin + "/*";
    chrome.permissions.contains({ origins: [origin] }, (has) => {
      if (chrome.runtime.lastError) {
        sendResponse({ ok: false, error: chrome.runtime.lastError.message });
        return;
      }
      if (has) {
        sendResponse({ ok: true });
        return;
      }
      chrome.permissions.request({ origins: [origin] }, (granted) => {
        if (chrome.runtime.lastError) {
          sendResponse({ ok: false, error: chrome.runtime.lastError.message });
          return;
        }
        sendResponse({
          ok: !!granted,
          error: granted ? null : "Permission denied — allow access to fill forms on this site.",
        });
      });
    });
  } catch (e) {
    sendResponse({ ok: false, error: e.message });
  }
}

function injectContentScript(tabId) {
  try {
    chrome.scripting.executeScript(
      { target: { tabId }, files: ["content.js"] },
      () => { void chrome.runtime.lastError; }
    );
  } catch { /* tab closed */ }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "ENSURE_HOST_PERMISSION") {
    ensureHostPermissionForUrl(msg.url, sendResponse);
    return true;
  }
  if (msg.type === "SUGGEST_ANSWERS") {
    // Fetch profile server-side (don't trust page), then suggest
    chrome.storage.local.get(["autoapply_token"], (r) => {
      const token = r.autoapply_token;
      if (!token) {
        sendResponse({ ok: false, error: "No auth token" });
        return;
      }
      suggestAnswers(token, msg.fields).then(sendResponse);
    });
    return true;
  }
  if (msg.type === "FETCH_PROFILE_FOR_FILL") {
    chrome.storage.local.get(["autoapply_token"], (r) => {
      const token = r.autoapply_token;
      if (!token) {
        sendResponse({ ok: false, error: "No auth token" });
        return;
      }
      fetchProfile(token).then(sendResponse);
    });
    return true;
  }
  if (msg.type === "PULL_TOKEN_FROM_APP_TAB") {
    // Triggered by content.js when the user lands on a job page via
    // "Apply with Autofill" but the extension has no JWT yet. Pulls the JWT
    // from any open autoapplynow.in tab so the upcoming smart-fill works.
    chrome.tabs.query(
      { url: ["https://autoapplynow.in/*", "https://mango-ocean-0f1de6810.2.azurestaticapps.net/*"] },
      (tabs) => {
        if (!tabs || tabs.length === 0) { sendResponse({ ok: false, error: "no app tab" }); return; }
        const tabId = tabs[0].id;
        try {
          chrome.scripting.executeScript(
            {
              target: { tabId },
              func: () => {
                try { return localStorage.getItem("auth_token"); } catch { return null; }
              },
            },
            (results) => {
              void chrome.runtime.lastError;
              const tok = results?.[0]?.result;
              if (tok) {
                chrome.storage.local.set({ autoapply_token: tok }, () => sendResponse({ ok: true }));
              } else {
                sendResponse({ ok: false, error: "no token in app tab" });
              }
            }
          );
        } catch (e) {
          sendResponse({ ok: false, error: e.message });
        }
      }
    );
    return true;
  }
  if (msg.type === "SAVE_CUSTOM_ANSWERS") {
    chrome.storage.local.get(["autoapply_token"], (r) => {
      const token = r.autoapply_token;
      if (!token) {
        sendResponse({ ok: false, error: "No auth token" });
        return;
      }
      saveCustomAnswers(token, msg.answers).then(sendResponse);
    });
    return true;
  }
});

async function fetchProfile(token) {
  try {
    const API_BASE = await getApiBase();
    const resp = await fetch(`${API_BASE}/api/v1/autofill/profile`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) {
      const text = await resp.text();
      return { ok: false, error: `HTTP ${resp.status}: ${text.slice(0, 100)}` };
    }
    const data = await resp.json();
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: e.name === "TimeoutError" ? "Backend timed out (15s)" : e.message };
  }
}

async function suggestAnswers(token, fields) {
  try {
    const API_BASE = await getApiBase();
    const resp = await fetch(`${API_BASE}/api/v1/autofill/suggest`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ fields }),
      signal: AbortSignal.timeout(25000),
    });
    if (!resp.ok) {
      const text = await resp.text();
      if (resp.status === 429) {
        // Parse server upgrade message
        let upgradeMsg = "Daily AI autofill limit reached. Upgrade to Premium for unlimited.";
        try {
          const errData = JSON.parse(text);
          if (errData.error?.message) upgradeMsg = errData.error.message;
          else if (errData.message) upgradeMsg = errData.message;
        } catch {}
        return { ok: false, rateLimited: true, error: upgradeMsg };
      }
      return { ok: false, error: `HTTP ${resp.status}: ${text.slice(0, 100)}` };
    }
    const data = await resp.json();
    return { ok: true, answers: data.answers || [] };
  } catch (e) {
    return { ok: false, error: e.name === "TimeoutError" ? "AI request timed out (25s) — try again" : e.message };
  }
}

// ── Auto-login bridge ─────────────────────────────────────────────────────
// When the user navigates to (or finishes loading) autoapplynow.in we pull
// localStorage.auth_token directly from the page and stash it in
// chrome.storage.local.autoapply_token. This is a defence-in-depth layer on
// top of the content script bridge — it covers the cases where the content
// script hasn't finished loading yet (popup opened too early) or where a
// stale extension session is still showing the sign-in screen.
const APP_URL_FILTERS = {
  url: [
    { hostEquals: "autoapplynow.in" },
    { hostEquals: "www.autoapplynow.in" },
    { hostEquals: "mango-ocean-0f1de6810.2.azurestaticapps.net" },
  ],
};

function pullTokenFromAppTab(tabId) {
  try {
    chrome.scripting.executeScript(
      {
        target: { tabId },
        func: () => {
          try { return localStorage.getItem("auth_token"); } catch { return null; }
        },
      },
      (results) => {
        void chrome.runtime.lastError;
        const tok = results?.[0]?.result;
        if (tok) {
          chrome.storage.local.set({ autoapply_token: tok });
        }
      }
    );
  } catch { /* tab gone / no permission — ignore */ }
}

chrome.tabs?.onUpdated.addListener((tabId, info, tab) => {
  if (info.status !== "complete") return;
  if (!tab?.url) return;
  try {
    const u = new URL(tab.url);
    const host = u.hostname;
    if (
      host === "autoapplynow.in" ||
      host === "www.autoapplynow.in" ||
      host === "mango-ocean-0f1de6810.2.azurestaticapps.net"
    ) {
      pullTokenFromAppTab(tabId);
    }
    // "Apply with Autofill" from the web app on a custom career domain:
    // content_scripts may not match — request optional permission and inject.
    if (/__autoapply/.test(tab.url)) {
      ensureHostPermissionForUrl(tab.url, (resp) => {
        if (resp?.ok) injectContentScript(tabId);
      });
    }
  } catch { /* malformed URL */ }
});

// On install/update: scan any open autoapplynow.in tabs and pull the token.
chrome.runtime.onInstalled.addListener(() => {
  chrome.tabs?.query(
    { url: ["https://autoapplynow.in/*", "https://mango-ocean-0f1de6810.2.azurestaticapps.net/*"] },
    (tabs) => {
      (tabs || []).forEach((t) => t.id && pullTokenFromAppTab(t.id));
    }
  );
});

async function saveCustomAnswers(token, answers) {
  try {
    const API_BASE = await getApiBase();
    const resp = await fetch(`${API_BASE}/api/v1/autofill/save-answers`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ answers }),
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) {
      const text = await resp.text();
      return { ok: false, error: `HTTP ${resp.status}: ${text.slice(0, 100)}` };
    }
    const data = await resp.json();
    return { ok: true, saved: data.saved || 0, totalRemembered: data.totalRemembered || 0 };
  } catch (e) {
    return { ok: false, error: e.name === "TimeoutError" ? "Save timed out (15s)" : e.message };
  }
}
