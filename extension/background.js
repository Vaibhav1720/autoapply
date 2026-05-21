/* background.js — AutoApply Chrome Extension service worker */

// Production backend (Flex Consumption Function App). The Flutter web app at
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

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
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
