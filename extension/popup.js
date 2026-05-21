/* popup.js — AutoApply v1.5 standalone popup (signup/login/profile) */

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
// Same backend as the Flutter web app — a Google sign-in here gives access
// to the resume / saved answers uploaded on the web (and vice versa).
const DEFAULT_API_BASE = "https://autoapply-func-dev.azurewebsites.net";
const LEGACY_API_BASE = "https://autoapply-func-dev.azurewebsites.net";
const PRIVACY_URL = "https://mango-ocean-0f1de6810.2.azurestaticapps.net/privacy.html";

let API_BASE = DEFAULT_API_BASE;

function setStatus(msg, kind) {
  const el = $("#status");
  el.textContent = msg;
  el.className = "status " + (kind || "info");
}

function show(id) {
  $("#authBlock").style.display = "none";
  $("#mainBlock").style.display = "none";
  $("#settingsBlock").style.display = "none";
  $(id).style.display = "block";
}

async function loadApiBase() {
  return new Promise((res) => {
    chrome.storage.local.get(["autoapply_api_base"], (r) => {
      const stored = r.autoapply_api_base;
      // Auto-migrate stale pin to the legacy backend so the popup uses the
      // shared one without forcing the user into Settings.
      if (stored === LEGACY_API_BASE) {
        chrome.storage.local.set({ autoapply_api_base: DEFAULT_API_BASE });
        API_BASE = DEFAULT_API_BASE;
      } else {
        API_BASE = stored || DEFAULT_API_BASE;
      }
      res(API_BASE);
    });
  });
}

async function apiFetch(path, opts = {}) {
  const url = `${API_BASE}${path}`;
  const resp = await fetch(url, opts);
  let data = null;
  try { data = await resp.json(); } catch (_) {}
  if (!resp.ok) {
    const msg = (data && (data.error?.message || data.message || data.error)) || `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data;
}

// ---- Boot ----
(async function init() {
  await loadApiBase();
  $("#privacyLink").href = PRIVACY_URL;

  // First try to auto-pull a token from any open autoapplynow.in tab
  // (covers the case where the user signed in on the web app but the
  // content-script bridge hasn't fired yet for this popup session).
  await tryPullTokenFromAppTab();

  chrome.storage.local.get(["autoapply_token"], async (r) => {
    if (r.autoapply_token) {
      await loadProfile(r.autoapply_token);
    } else {
      show("#authBlock");
      setStatus("Sign in on autoapplynow.in or click below.", "info");
    }
  });
})();

// Auto-refresh when the content script (running on autoapplynow.in) pushes
// a freshly synced token into chrome.storage. Without this, the popup would
// keep showing "Sign in" until the user closed and re-opened it.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.autoapply_token) {
    const newTok = changes.autoapply_token.newValue;
    const authVisible = $("#authBlock").style.display !== "none";
    if (newTok && authVisible) {
      // We were on the sign-in screen and a token just arrived — log in.
      setStatus("✓ Detected sign-in from web app…", "ok");
      loadProfile(newTok);
    } else if (!newTok) {
      // Token was cleared (user signed out on the web) — drop to sign-in.
      show("#authBlock");
      setStatus("Signed out.", "info");
    }
  }
});

// Ask any open autoapplynow.in tab to share its JWT with the extension.
// This is a backup path for the content-script bridge: if the user signed in
// on the web app before installing the extension (or before opening the
// popup for the first time), this pulls the token without requiring them
// to refresh the web tab.
async function tryPullTokenFromAppTab() {
  return new Promise((resolve) => {
    if (!chrome.tabs?.query || !chrome.scripting?.executeScript) {
      resolve();
      return;
    }
    chrome.tabs.query(
      { url: ["https://autoapplynow.in/*", "https://mango-ocean-0f1de6810.2.azurestaticapps.net/*"] },
      (tabs) => {
        if (!tabs || tabs.length === 0) { resolve(); return; }
        const tabId = tabs[0].id;
        chrome.scripting.executeScript(
          {
            target: { tabId },
            func: () => {
              try { return localStorage.getItem("auth_token"); } catch { return null; }
            },
          },
          (results) => {
            // Swallow any access error — fall through to manual sign-in.
            void chrome.runtime.lastError;
            const tok = results?.[0]?.result;
            if (tok) {
              chrome.storage.local.set({ autoapply_token: tok }, resolve);
            } else {
              resolve();
            }
          }
        );
      }
    );
  });
}

// ---- Google sign-in ----
// Web OAuth client ID (configured in Google Cloud Console). Replace with your
// own value once you create the OAuth client per the README. The same web
// client works for the extension via chrome.identity.launchWebAuthFlow + the
// chromiumapp.org redirect URI.
const GOOGLE_CLIENT_ID = "8017795829-np8cfekibbnfr960rfo6fqj6g8kl8dm1.apps.googleusercontent.com";

function buildGoogleAuthUrl(redirectUri, nonce) {
  const params = new URLSearchParams({
    client_id: GOOGLE_CLIENT_ID,
    response_type: "id_token",
    scope: "openid email profile",
    redirect_uri: redirectUri,
    nonce,
    prompt: "select_account",
  });
  return `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`;
}

function extractIdTokenFromUrl(url) {
  // launchWebAuthFlow returns the full redirect URL with the id_token in the
  // URL fragment (#id_token=...).
  const i = url.indexOf("#");
  if (i < 0) return null;
  const params = new URLSearchParams(url.substring(i + 1));
  return params.get("id_token");
}

$("#btnGoogleLogin").addEventListener("click", async () => {
  if (GOOGLE_CLIENT_ID.startsWith("REPLACE_ME") || GOOGLE_CLIENT_ID.includes("<your-google-client-id>")) {
    setStatus("✗ Google sign-in not configured — run tools/configure-extension.sh or set GOOGLE_CLIENT_ID in popup.js.", "err");
    return;
  }
  $("#btnGoogleLogin").disabled = true;
  setStatus("Opening Google sign-in…", "info");
  try {
    const redirectUri = chrome.identity.getRedirectURL();
    const nonce = (crypto.getRandomValues(new Uint32Array(2)).join(""));
    const authUrl = buildGoogleAuthUrl(redirectUri, nonce);
    const responseUrl = await new Promise((resolve, reject) => {
      chrome.identity.launchWebAuthFlow(
        { url: authUrl, interactive: true },
        (responseUrl) => {
          if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
          if (!responseUrl) return reject(new Error("Sign-in cancelled"));
          resolve(responseUrl);
        }
      );
    });
    const idToken = extractIdTokenFromUrl(responseUrl);
    if (!idToken) throw new Error("Google did not return an ID token");
    setStatus("Verifying with backend…", "info");
    const data = await apiFetch("/api/v1/auth/google", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idToken }),
    });
    const token = data.token || data.data?.token;
    if (!token) throw new Error("No token returned from backend");
    await new Promise((r) => chrome.storage.local.set({ autoapply_token: token }, r));
    setStatus("✓ Signed in. Upload your resume next.", "ok");
    await loadProfile(token);
    // First-run convenience: if the profile has no resume yet, open the
    // options page so the user can upload one straight away.
    try {
      const prof = await apiFetch("/api/v1/profile", { headers: { Authorization: `Bearer ${token}` } });
      const p = prof.data || prof;
      const hasResume = !!(p?.documents?.resumeUrl);
      if (!hasResume) setTimeout(() => chrome.runtime.openOptionsPage(), 600);
    } catch (_) { /* non-fatal */ }
  } catch (e) {
    setStatus("✗ " + e.message, "err");
  } finally {
    $("#btnGoogleLogin").disabled = false;
  }
});

// ---- Manual token (advanced) ----
$("#btnSaveToken").addEventListener("click", async () => {
  const tok = $("#manualToken").value.trim();
  if (!tok) return;
  await new Promise((r) => chrome.storage.local.set({ autoapply_token: tok }, r));
  $("#manualToken").value = "";
  await loadProfile(tok);
});

// ---- Load profile ----
async function loadProfile(token) {
  setStatus("Connecting…", "info");
  try {
    const profile = await apiFetch("/api/v1/autofill/profile", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const fullName = profile.fullName || profile.firstName || profile.email || "User";
    $("#userName").textContent = fullName;
    $("#userEmail").textContent = profile.email || "";
    setStatus(`✓ Connected`, "ok");
    show("#mainBlock");
    // Fetch missing-info to show completeness
    try {
      const mi = await apiFetch("/api/v1/profile/missing-info", {
        headers: { Authorization: `Bearer ${token}` },
      });
      const total = mi.totalCommon ?? 0;
      const missing = mi.missing || [];
      const filled = Math.max(0, total - missing.length);
      $("#completenessText").textContent = missing.length > 0
        ? `${filled}/${total} required fields saved · ${missing.length} missing`
        : `✓ ${filled} required fields saved`;
      // Surface the actual missing fields so the user can fix them right away.
      const banner = $("#missingBanner");
      const list = $("#missingList");
      if (banner && list) {
        if (missing.length > 0) {
          const labels = missing.slice(0, 6).map((m) => m.label || m.key).join(", ");
          const more = missing.length > 6 ? ` +${missing.length - 6} more` : "";
          list.textContent = `Missing: ${labels}${more}. AutoApply will ask you on every form until these are saved.`;
          banner.style.display = "block";
        } else {
          banner.style.display = "none";
        }
      }
      window.__autoapplyMissing = missing;
    } catch (_) {}
  } catch (e) {
    chrome.storage.local.remove("autoapply_token");
    show("#authBlock");
    setStatus("✗ " + e.message, "err");
  }
}

// ---- Logout ----
$("#btnLogout").addEventListener("click", () => {
  chrome.storage.local.remove("autoapply_token", () => {
    show("#authBlock");
    setStatus("Signed out.", "info");
  });
});

// ---- Open options page ----
$("#btnOpenOptions").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});

// ---- Fix missing details (banner button) ----
const fixBtn = document.getElementById("btnFixMissing");
if (fixBtn) {
  fixBtn.addEventListener("click", () => chrome.runtime.openOptionsPage());
}

// ---- Settings ----
$("#btnSettings").addEventListener("click", () => {
  $("#apiBase").value = API_BASE;
  show("#settingsBlock");
  setStatus("Configure backend.", "info");
});
$("#btnSaveSettings").addEventListener("click", () => {
  const v = $("#apiBase").value.trim().replace(/\/$/, "");
  if (!v) return;
  chrome.storage.local.set({ autoapply_api_base: v }, () => {
    API_BASE = v;
    setStatus("✓ Settings saved.", "ok");
    setTimeout(() => location.reload(), 500);
  });
});
$("#btnResetSettings").addEventListener("click", () => {
  chrome.storage.local.remove("autoapply_api_base", () => {
    API_BASE = DEFAULT_API_BASE;
    $("#apiBase").value = DEFAULT_API_BASE;
    setStatus("✓ Reset to default.", "ok");
  });
});
$("#btnCloseSettings").addEventListener("click", () => location.reload());

// ---- Fill actions: send to active tab ----
function ensureHostPermission(tabUrl) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "ENSURE_HOST_PERMISSION", url: tabUrl }, (resp) => {
      void chrome.runtime.lastError;
      resolve(resp?.ok);
    });
  });
}

function sendToContent(type, callback) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]) { callback?.({ filled: 0, error: "No active tab" }); return; }
    const tabId = tabs[0].id;
    const tabUrl = tabs[0].url || "";
    ensureHostPermission(tabUrl).then((granted) => {
      if (!granted) {
        callback?.({
          filled: 0,
          error: "Allow AutoApply to access this site when Chrome prompts you, then try again.",
        });
        return;
      }
    const inject = () => new Promise((resolve) => {
      let done = false;
      const finish = (frames) => { if (done) return; done = true; resolve(frames); };
      // Hard timeout — chrome.scripting.executeScript can hang forever if a
      // child frame is still loading or the page has dozens of cross-origin
      // iframes (Netflix careers, Workday widgets, etc.).
      setTimeout(() => finish([0]), 6000); // fall back to top frame only
      chrome.scripting.executeScript(
        { target: { tabId, allFrames: true }, files: ["content.js"] },
        (results) => {
          void chrome.runtime.lastError;
          const frames = (results || []).map((r) => r.frameId);
          finish(frames.length ? frames : [0]);
        }
      );
    });
    const askFrames = (frameIds) => new Promise((resolve) => {
      if (!frameIds.length) { resolve({ filled: 0, error: "No frames available" }); return; }
      let best = { filled: 0, aiCount: 0, asked: 0 };
      let pending = frameIds.length;
      let lastError = null;
      let resolved = false;
      const score = (r) => (r?.filled || 0) + (r?.aiCount || 0) + (r?.asked || 0);
      const finish = () => {
        if (resolved) return;
        resolved = true;
        if (!score(best) && lastError) best = { filled: 0, error: lastError };
        console.log("[AutoApply popup] frames=", frameIds.length, "best=", best);
        resolve(best);
      };
      // Hard ceiling: even if a frame's content script never replies (CSP /
      // sandboxed iframe / hung fetch in background), surface what we have
      // after 30 s instead of leaving the popup spinning forever.
      const hardTimer = setTimeout(finish, 30000);
      frameIds.forEach((frameId) => {
        chrome.tabs.sendMessage(tabId, { type }, { frameId }, (resp) => {
          const err = chrome.runtime.lastError;
          if (err) lastError = err.message;
          if (resp && score(resp) > score(best)) best = resp;
          else if (resp?.error && !score(best)) lastError = resp.error;
          pending--;
          if (pending === 0) { clearTimeout(hardTimer); finish(); }
        });
      });
    });
    // First pass
    inject().then((frames) => {
      console.log("[AutoApply popup] initial frames injected:", frames);
      askFrames(frames).then((first) => {
        if ((first.filled || 0) + (first.aiCount || 0) + (first.asked || 0) > 0) {
          callback?.(first);
          return;
        }
        // Second pass: late-loading iframe (Greenhouse, etc.). Wait, re-inject, retry.
        setTimeout(() => {
          inject().then((frames2) => {
            console.log("[AutoApply popup] retry frames injected:", frames2);
            askFrames(frames2).then(callback);
          });
        }, 2500);
      });
    });
    });
  });
}

$("#btnSmartFill").addEventListener("click", () => {
  $("#btnSmartFill").disabled = true;
  const start = Date.now();
  setStatus("Reading form fields…", "info");
  // Live status: tick every second so the user can see we're not frozen.
  const phases = [
    { at: 1500, msg: "Asking AI for answers…" },
    { at: 8000, msg: "Still working — large form, hang on…" },
    { at: 20000, msg: "Almost done — finalizing answers…" },
  ];
  const ticker = setInterval(() => {
    const elapsed = Date.now() - start;
    const phase = [...phases].reverse().find((p) => elapsed >= p.at);
    setStatus(`${phase ? phase.msg : "Reading form fields…"} (${Math.round(elapsed / 1000)}s)`, "info");
  }, 1000);
  sendToContent("SMART_FILL_NOW", (resp) => {
    clearInterval(ticker);
    $("#btnSmartFill").disabled = false;
    if (resp?.filled || resp?.aiCount) {
      const parts = [`${resp.filled || 0} filled`];
      if (resp.aiCount) parts.push(`${resp.aiCount} via AI`);
      if (resp.asked) parts.push(`${resp.asked} asked`);
      setStatus(`✓ ${parts.join(" • ")}`, "ok");
    } else {
      const err = resp?.error || "";
      if (/invalidated|context/i.test(err)) {
        setStatus("⚠️ Reload the page (F5), then try again.", "err");
      } else if (/no form|wait for it/i.test(err)) {
        setStatus("⚠️ Page still loading. Wait for the form to appear, then click Autofill again.", "err");
      } else {
        setStatus(err || "No fields filled", "err");
      }
    }
  });
});
