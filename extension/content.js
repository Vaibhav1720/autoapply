/* content.js — AutoApply form detection, rule-based fill, and smart fill */

(() => {
  // Version guard: re-inject overrides older instances. Bump when shipping
  // breaking content-script changes so popup-driven re-injection picks up
  // the new code instead of being blocked by a stale __autoapplyInjected flag.
  const CONTENT_SCRIPT_VERSION = "1.7.0";
  if (window.__autoapplyVersion === CONTENT_SCRIPT_VERSION) return;
  // A stale older copy may have left a dead FAB attached to the page whose
  // chrome.runtime handle is invalid after extension reload. Remove it so we
  // can install a fresh one bound to the current extension context.
  try {
    const stale = document.getElementById("__autoapply_fab");
    if (stale && window.__autoapplyVersion && window.__autoapplyVersion !== CONTENT_SCRIPT_VERSION) {
      stale.remove();
    }
  } catch { /* ignore */ }
  window.__autoapplyVersion = CONTENT_SCRIPT_VERSION;
  window.__autoapplyInjected = true;

  // ── Helpers ────────────────────────────────────────────────────────────

  function isContextValid() {
    try { return !!chrome.runtime?.id; } catch { return false; }
  }

  function safeSendMessage(msg, timeoutMs = 25000) {
    return new Promise((resolve) => {
      if (!isContextValid()) { resolve({ ok: false, error: "Extension invalidated" }); return; }
      let done = false;
      const finish = (v) => { if (done) return; done = true; resolve(v); };
      const t = setTimeout(() => finish({ ok: false, error: `Timed out after ${timeoutMs}ms` }), timeoutMs);
      try {
        chrome.runtime.sendMessage(msg, (resp) => {
          clearTimeout(t);
          if (chrome.runtime.lastError) { finish({ ok: false, error: chrome.runtime.lastError.message }); return; }
          finish(resp || { ok: false });
        });
      } catch { clearTimeout(t); finish({ ok: false, error: "Extension context invalidated" }); }
    });
  }

  // ── Deep DOM traversal (shadow DOM + iframes) ──────────────────────────
  // Hard caps to prevent renderer crashes on giant pages (Workday, etc.)
  const MAX_FIELDS = 200;
  const MAX_NODES_TRAVERSED = 20000;
  const MAX_SHADOW_DEPTH = 8;
  const MAX_IFRAMES = 5;

  function traverseDeep(root, callback, state, depth = 0) {
    if (!root) return;
    if (depth > MAX_SHADOW_DEPTH) return;
    state = state || { nodes: 0, stop: false };
    let walker;
    try {
      walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    } catch { return; }
    let node = walker.currentNode;
    while (node && !state.stop) {
      state.nodes++;
      if (state.nodes > MAX_NODES_TRAVERSED) { state.stop = true; break; }
      try { callback(node, state); } catch { /* keep walking */ }
      if (node.shadowRoot) traverseDeep(node.shadowRoot, callback, state, depth + 1);
      node = walker.nextNode();
    }
    return state;
  }

  function getAllFields(root = document) {
    const fields = [];
    const state = { nodes: 0, stop: false };
    traverseDeep(root, (el, s) => {
      if (fields.length >= MAX_FIELDS) { s.stop = true; return; }
      if (el.matches?.("input, textarea, select") && !el.disabled && !el.readOnly) {
        const type = el.type?.toLowerCase() || "";
        if (["hidden", "submit", "button", "reset", "image", "file"].includes(type)) return;
        fields.push(el);
      }
    }, state);
    // Also check same-origin iframes — capped
    if (fields.length < MAX_FIELDS) {
      try {
        const iframes = Array.from(document.querySelectorAll("iframe")).slice(0, MAX_IFRAMES);
        for (const iframe of iframes) {
          try {
            const doc = iframe.contentDocument;
            if (doc) {
              const more = getAllFields(doc);
              for (const f of more) {
                if (fields.length >= MAX_FIELDS) break;
                fields.push(f);
              }
            }
          } catch { /* cross-origin — skip */ }
        }
      } catch { }
    }
    return fields;
  }

  // ── Label detection ────────────────────────────────────────────────────

  function fieldHints(el) {
    const hints = [];
    const rootNode = el.getRootNode();

    // 1. label[for]
    const id = el.id || el.getAttribute("name");
    if (id) {
      const label = rootNode.querySelector?.(`label[for="${CSS.escape(id)}"]`);
      if (label) hints.push(label.textContent.trim());
    }

    // 2. Closest wrapping <label>
    const parentLabel = el.closest?.("label");
    if (parentLabel) hints.push(parentLabel.textContent.trim());

    // 3. aria-labelledby
    const alb = el.getAttribute("aria-labelledby");
    if (alb) {
      const labelEl = rootNode.getElementById?.(alb);
      if (labelEl) hints.push(labelEl.textContent.trim());
    }

    // 4. aria-describedby
    const adb = el.getAttribute("aria-describedby");
    if (adb) {
      const descEl = rootNode.getElementById?.(adb);
      if (descEl) hints.push(descEl.textContent.trim());
    }

    // 5. aria-label, title, placeholder, name
    if (el.getAttribute("aria-label")) hints.push(el.getAttribute("aria-label"));
    if (el.title) hints.push(el.title);
    if (el.placeholder) hints.push(el.placeholder);
    if (el.name) hints.push(el.name);

    // 6. Previous sibling text
    const prev = el.previousElementSibling;
    if (prev && (prev.tagName === "LABEL" || prev.tagName === "SPAN" || prev.tagName === "P")) {
      hints.push(prev.textContent.trim());
    }

    return hints.map(h => h.replace(/\s+/g, " ").slice(0, 100).toLowerCase()).filter(Boolean);
  }

  // ── Rule-based mapping ─────────────────────────────────────────────────

  const RULES = [
    { pattern: /first.?name|given.?name|forename/i, key: "firstName" },
    { pattern: /last.?name|surname|family.?name/i, key: "lastName" },
    { pattern: /full.?name|^name$/i, key: "fullName" },
    { pattern: /e.?mail/i, key: "email" },
    { pattern: /phone|mobile|tel/i, key: "phone" },
    { pattern: /linkedin/i, key: "linkedinUrl" },
    { pattern: /github/i, key: "githubUrl" },
    { pattern: /portfolio|personal.?website/i, key: "portfolioUrl" },
    { pattern: /^city$/i, key: "city" },
    { pattern: /state|province/i, key: "state" },
    { pattern: /zip|postal/i, key: "zip" },
    { pattern: /country/i, key: "country" },
    { pattern: /address|street/i, key: "address" },
    { pattern: /salary|compensation|expected.?pay/i, key: "salaryExpectation" },
    { pattern: /notice.?period|earliest.?start|availability/i, key: "noticePeriod" },
    { pattern: /visa|work.?auth|sponsor/i, key: "visaStatus" },
    { pattern: /relocat/i, key: "willingToRelocate" },
    { pattern: /gender|sex/i, key: "gender" },
    { pattern: /veteran|military/i, key: "veteranStatus" },
    { pattern: /disab/i, key: "disability" },
    { pattern: /ethni|race/i, key: "ethnicity" },
    { pattern: /university|school|college/i, key: "university" },
    { pattern: /degree|qualification/i, key: "degree" },
    { pattern: /graduat|year.?of.?study|completion/i, key: "graduationYear" },
    { pattern: /current.?title|job.?title|^position$/i, key: "currentTitle" },
    { pattern: /current.?company|current.?employer/i, key: "currentCompany" },
    { pattern: /years?.?(?:of)?.?experience|experience.?years/i, key: "experienceYears" },
    { pattern: /skill|technolog|expertise/i, key: "skills" },
    { pattern: /summary|professional.?summary|objective/i, key: "summary" },
    { pattern: /cover.?letter|motivation|why.?(?:this|apply|interested)|about.?you/i, key: "coverLetter" },
  ];

  function matchRule(hints) {
    for (const hint of hints) {
      for (const rule of RULES) {
        if (rule.pattern.test(hint)) return rule.key;
      }
    }
    return null;
  }

  // ── Native value setting ───────────────────────────────────────────────

  function setNativeValue(el, value) {
    if (!value && value !== 0) return false;
    const str = String(value);

    if (el.tagName === "SELECT") {
      const options = Array.from(el.options);
      const match = options.find(o => o.value.toLowerCase() === str.toLowerCase() || o.text.toLowerCase() === str.toLowerCase())
        || options.find(o => o.text.toLowerCase().includes(str.toLowerCase()) || str.toLowerCase().includes(o.text.toLowerCase()));
      if (match) {
        el.value = match.value;
        el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
        return true;
      }
      return false;
    }

    // Use native setter to trigger React/Angular change detection
    const nativeSetter = Object.getOwnPropertyDescriptor(
      el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value"
    )?.set;
    if (nativeSetter) {
      nativeSetter.call(el, str);
    } else {
      el.value = str;
    }
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return true;
  }

  // ── Autofill (rule-based) ──────────────────────────────────────────────

  // Wait up to `maxMs` for at least one fillable field to appear (handles
  // SPA-hydrated forms like Stripe / Lever / Workday that render client-side
  // after the initial HTML has loaded). 4 s is enough for every adapter we've
  // seen; longer just made the popup feel frozen on static pages without forms.
  function waitForFields(maxMs = 8000) {
    return new Promise((resolve) => {
      const start = Date.now();
      const initial = getAllFields();
      if (initial.length > 0) { resolve(initial); return; }
      let mo = null;
      const finish = (fields) => {
        if (mo) try { mo.disconnect(); } catch { /* ignore */ }
        resolve(fields);
      };
      mo = new MutationObserver(() => {
        if (Date.now() - start > maxMs) { finish(getAllFields()); return; }
        const f = getAllFields();
        if (f.length > 0) finish(f);
      });
      try {
        mo.observe(document.documentElement, { childList: true, subtree: true });
      } catch { /* ignore */ }
      // Hard timeout fallback in case no mutations fire
      setTimeout(() => finish(getAllFields()), maxMs);
    });
  }

  async function doAutofill(preFields) {
    const resp = await safeSendMessage({ type: "FETCH_PROFILE_FOR_FILL" });
    if (!resp.ok) return { filled: 0, error: resp.error || "Failed to load profile", fields: [] };

    const profile = resp.data;
    const fields = preFields && preFields.length ? preFields : await waitForFields();
    let filled = 0;
    if (!fields.length) return { filled: 0, error: "No form fields detected on this page (yet)", fields };

    for (const el of fields) {
      if (el.value && el.value.trim()) continue; // Skip already filled
      const hints = fieldHints(el);
      const key = matchRule(hints);
      if (key && profile[key]) {
        if (setNativeValue(el, profile[key])) {
          el.dataset.autoapplyFilled = "rule";
          filled++;
        }
      }
    }
    return { filled, fields };
  }

  // ── Smart fill (rules + AI) ────────────────────────────────────────────

  const CONFIDENCE_THRESHOLD = 0.8;

  async function doSmartFill() {
    console.log("[AutoApply] doSmartFill start in frame:", location.href, "isTop=", window.top===window.self);
    // Step 1: Wait once for fields to appear, then run rule-based fill.
    const fields = await waitForFields();
    console.log("[AutoApply] total fields detected:", fields.length);
    if (!fields.length) return { filled: 0, aiCount: 0, asked: 0, error: "This page has no form yet — wait for it to finish loading (or click the Apply button), then try again." };
    const ruleResult = await doAutofill(fields);
    console.log("[AutoApply] rule fill result:", ruleResult);

    // Step 2: Collect still-empty fields and remember which DOM element each key maps to
    const emptyFields = [];
    const keyToEl = new Map();
    let keyIdx = 0;

    for (const el of fields) {
      if (el.value && el.value.trim()) continue;
      const hints = fieldHints(el);
      if (!hints.length) continue;

      const key = `llm_field_${keyIdx++}`;
      el.dataset.llmAutofillKey = key;
      keyToEl.set(key, el);

      const fieldInfo = { key, label: hints.join(" | "), type: el.type || el.tagName.toLowerCase() };
      if (el.tagName === "SELECT") {
        fieldInfo.options = Array.from(el.options).map(o => o.text).filter(Boolean).slice(0, 30);
      }
      if (el.maxLength > 0) fieldInfo.maxLength = el.maxLength;
      emptyFields.push(fieldInfo);
    }

    if (!emptyFields.length) return { filled: ruleResult.filled, aiCount: 0, asked: 0 };

    // Step 3: Send to AI via background → backend
    const aiResp = await safeSendMessage({ type: "SUGGEST_ANSWERS", fields: emptyFields });

    // Handle rate limiting (free-tier daily cap)
    if (aiResp.rateLimited) {
      showUpgradeToast(aiResp.error || "Daily AI autofill limit reached. Upgrade to Premium for unlimited.");
      return { filled: ruleResult.filled, aiCount: 0, asked: 0 };
    }

    let aiCount = 0;
    const lowConfidence = []; // {key, label, value(suggestion), confidence, reasoning, options, type, maxLength}

    if (aiResp.ok && aiResp.answers) {
      for (const ans of aiResp.answers) {
        const target = keyToEl.get(ans.key);
        if (!target) continue;
        const conf = typeof ans.confidence === "number" ? ans.confidence : 0.5;
        const value = (ans.value || "").toString();
        const fieldInfo = emptyFields.find(f => f.key === ans.key) || {};

        if (value && conf >= CONFIDENCE_THRESHOLD) {
          if (setNativeValue(target, value)) {
            target.dataset.autoapplyFilled = ans.source || "ai";
            aiCount++;
          }
        } else {
          lowConfidence.push({
            key: ans.key,
            label: fieldInfo.label || "",
            value,
            confidence: conf,
            reasoning: ans.reasoning || "",
            options: fieldInfo.options || null,
            type: fieldInfo.type || "text",
            maxLength: fieldInfo.maxLength || 0,
          });
        }
      }
    }

    if (lowConfidence.length) {
      renderAskPanel(lowConfidence, keyToEl);
    }

    return { filled: ruleResult.filled + aiCount, aiCount, asked: lowConfidence.length };
  }

  // ── Ask-the-user panel ─────────────────────────────────────────────────
  // For low-confidence / unknown fields we render an in-page side panel so the
  // user can fill them inline. On submit we (a) apply the values to the page
  // and (b) POST them back so future autofills are confident.

  function renderAskPanel(items, keyToEl) {
    const existing = document.getElementById("__autoapply_ask");
    if (existing) existing.remove();

    const panel = document.createElement("div");
    panel.id = "__autoapply_ask";
    Object.assign(panel.style, {
      position: "fixed", top: "20px", right: "20px", width: "380px",
      maxHeight: "80vh", overflowY: "auto", zIndex: "2147483647",
      background: "#fff", color: "#111827",
      border: "1px solid #E5E7EB", borderRadius: "12px",
      boxShadow: "0 12px 32px rgba(0,0,0,.18)",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      fontSize: "13px",
    });

    const header = document.createElement("div");
    header.style.cssText = "padding:14px 16px;border-bottom:1px solid #E5E7EB;display:flex;justify-content:space-between;align-items:center;";
    header.innerHTML = `
      <div>
        <div style="font-weight:600;font-size:14px">A few more details</div>
        <div style="color:#6B7280;font-size:11px;margin-top:2px">We weren't sure about ${items.length} field${items.length === 1 ? "" : "s"}. Your answers are saved for next time.</div>
      </div>
      <button id="__autoapply_ask_close" style="background:none;border:none;font-size:18px;cursor:pointer;color:#6B7280;padding:0 4px">×</button>
    `;
    panel.appendChild(header);

    const list = document.createElement("div");
    list.style.cssText = "padding:12px 16px;display:flex;flex-direction:column;gap:14px";
    items.forEach((item, idx) => {
      const row = document.createElement("div");
      const labelText = item.label.split(" | ")[0].slice(0, 120) || `Field ${idx + 1}`;
      const reasonText = item.reasoning || (item.value ? "We guessed this — please confirm." : "We couldn't find this in your profile.");
      let inputHtml;
      if (item.options && item.options.length) {
        inputHtml = `<select data-aakey="${item.key}" style="width:100%;padding:7px 9px;border:1px solid #D1D5DB;border-radius:6px;font-size:13px">
          <option value="">— select —</option>
          ${item.options.map(o => `<option ${o === item.value ? "selected" : ""}>${escapeHtml(o)}</option>`).join("")}
        </select>`;
      } else if ((item.maxLength && item.maxLength > 200) || /cover|why|motiv|tell|about|describe/i.test(item.label)) {
        inputHtml = `<textarea data-aakey="${item.key}" rows="3" style="width:100%;padding:7px 9px;border:1px solid #D1D5DB;border-radius:6px;font-size:13px;font-family:inherit;resize:vertical">${escapeHtml(item.value)}</textarea>`;
      } else {
        inputHtml = `<input data-aakey="${item.key}" value="${escapeHtml(item.value)}" placeholder="Your answer" style="width:100%;padding:7px 9px;border:1px solid #D1D5DB;border-radius:6px;font-size:13px">`;
      }
      row.innerHTML = `
        <div style="font-weight:500;margin-bottom:4px;color:#111827">${escapeHtml(labelText)}</div>
        <div style="color:#6B7280;font-size:11px;margin-bottom:6px">${escapeHtml(reasonText)}</div>
        ${inputHtml}
      `;
      list.appendChild(row);
    });
    panel.appendChild(list);

    const footer = document.createElement("div");
    footer.style.cssText = "padding:12px 16px;border-top:1px solid #E5E7EB;display:flex;gap:8px;background:#F9FAFB;border-radius:0 0 12px 12px";
    footer.innerHTML = `
      <button id="__autoapply_ask_skip" style="flex:1;padding:9px;border:1px solid #D1D5DB;background:#fff;border-radius:8px;cursor:pointer;font-size:13px;color:#374151">Skip</button>
      <button id="__autoapply_ask_save" style="flex:2;padding:9px;border:none;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600">Save & Fill</button>
    `;
    panel.appendChild(footer);
    document.body.appendChild(panel);

    panel.querySelector("#__autoapply_ask_close").addEventListener("click", () => panel.remove());
    panel.querySelector("#__autoapply_ask_skip").addEventListener("click", () => panel.remove());
    panel.querySelector("#__autoapply_ask_save").addEventListener("click", async () => {
      const saveBtn = panel.querySelector("#__autoapply_ask_save");
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving…";
      const toSave = [];
      let filled = 0;
      panel.querySelectorAll("[data-aakey]").forEach((input) => {
        const key = input.dataset.aakey;
        const val = (input.value || "").trim();
        if (!val) return;
        const item = items.find(i => i.key === key);
        const target = keyToEl.get(key);
        if (target && setNativeValue(target, val)) {
          target.dataset.autoapplyFilled = "user";
          filled++;
        }
        if (item && item.label) {
          toSave.push({ label: item.label.split(" | ")[0].slice(0, 200), value: val });
        }
      });
      if (toSave.length) {
        const resp = await safeSendMessage({ type: "SAVE_CUSTOM_ANSWERS", answers: toSave });
        if (resp.ok) {
          showToast(`Filled ${filled} more & remembered for next time (${resp.totalRemembered} saved).`, true);
        } else {
          showToast(`Filled ${filled} but couldn't save: ${resp.error || "unknown"}`, false);
        }
      } else {
        showToast("Nothing to save.", false);
      }
      panel.remove();
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ── Message listener ───────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type === "SMART_FILL_NOW") {
      doSmartFill().then(sendResponse);
      return true;
    }
  });

  // ── MutationObserver for dynamic forms ─────────────────────────────────

  let debounceTimer = null;
  const observer = new MutationObserver(() => {
    if (!isContextValid()) { observer.disconnect(); return; }
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      // Observe newly added shadow roots — bounded
      try {
        let registered = 0;
        traverseDeep(document, (el) => {
          if (registered >= 50) return;
          if (el.shadowRoot && !el.__autoapplyObserved) {
            el.__autoapplyObserved = true;
            registered++;
            try { observer.observe(el.shadowRoot, { childList: true, subtree: true }); } catch { /* ignore */ }
          }
        });
      } catch { /* ignore */ }
    }, 500);
  });

  if (document.documentElement) {
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }

  // ── Auto-fill on page open from app ────────────────────────────────────
  // When the app opens a job URL with #__autoapply, trigger autofill automatically
  if (location.hash.includes("__autoapply")) {
    // Clean the hash so it doesn't look weird
    const cleanUrl = location.href.replace(/#__autoapply.*$/, "").replace(/&__autoapply=1/, "");
    history.replaceState(null, "", cleanUrl || location.pathname);

    // Wait for the page to load forms, then auto-fill
    const autoFillDelay = () => {
      const fields = getAllFields();
      if (fields.length > 0) {
        doSmartFill().then((result) => {
          // Show a subtle notification
          const banner = document.createElement("div");
          banner.id = "__autoapply_banner";
          banner.innerHTML = result.filled > 0
            ? `✅ AutoApply filled <b>${result.filled}</b> fields${result.aiCount ? ` (${result.aiCount} via AI)` : ""}. Review and submit!`
            : `⚠️ AutoApply found no fillable fields on this page. Try clicking "Apply" first.`;
          Object.assign(banner.style, {
            position: "fixed", top: "12px", right: "12px", zIndex: "999999",
            background: result.filled > 0 ? "#059669" : "#D97706", color: "#fff",
            padding: "12px 20px", borderRadius: "10px", fontSize: "14px",
            fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
            boxShadow: "0 4px 12px rgba(0,0,0,.2)", maxWidth: "400px",
            animation: "fadeIn .3s ease",
          });
          document.body.appendChild(banner);
          setTimeout(() => banner.remove(), 8000);
        });
      } else {
        // No fields yet — might be a SPA, retry after DOM settles
        setTimeout(autoFillDelay, 2000);
      }
    };
    // Give the page 1.5s to load its forms
    setTimeout(autoFillDelay, 1500);
  }

  // ── AutoApply App Integration ──────────────────────────────────────────
  // When running on the AutoApply app, auto-sync JWT + signal extension presence

  const APP_HOSTS = [
    "autoapply-func-dev.azurewebsites.net",
    "autoapply-func-dev.azurewebsites.net",
    "localhost",
    "azurestaticapps.net",
  ];
  const isAppPage = APP_HOSTS.some(h => location.hostname.includes(h)) ||
    document.querySelector('meta[name="autoapply-app"]');

  // Signal extension is installed (any page can check via DOM)
  document.documentElement.setAttribute("data-autoapply-ext", "installed");

  // Listen for token sync requests from the Flutter app
  window.addEventListener("message", (event) => {
    if (!isContextValid()) return;
    if (event.source !== window) return;

    if (event.data?.type === "AUTOAPPLY_SYNC_TOKEN" && event.data.token) {
      chrome.storage.local.set({ autoapply_token: event.data.token }, () => {
        window.postMessage({ type: "AUTOAPPLY_TOKEN_SYNCED", ok: true }, "*");
      });
    }

    if (event.data?.type === "AUTOAPPLY_CHECK_EXTENSION") {
      window.postMessage({ type: "AUTOAPPLY_EXTENSION_STATUS", installed: true }, "*");
    }
  });

  // Auto-sync: if on the app page, grab token from localStorage (read-only; we
  // do NOT monkey-patch localStorage.setItem because that runs on every page
  // and can crash analytics-heavy sites or cause infinite loops.
  if (isAppPage) {
    try {
      const token = localStorage.getItem("auth_token");
      if (token) {
        chrome.storage.local.set({ autoapply_token: token }, () => {
          document.documentElement.setAttribute("data-autoapply-ext", "connected");
        });
      }
    } catch { /* localStorage access blocked */ }
    // Re-check periodically while on the app page
    setInterval(() => {
      if (!isContextValid()) return;
      try {
        const token = localStorage.getItem("auth_token");
        if (token) chrome.storage.local.set({ autoapply_token: token });
        else chrome.storage.local.remove("autoapply_token");
      } catch { /* ignore */ }
    }, 5000);
  }

  // ── Collapsible floating action button ───────────────────────────────
  // A persistent, minimal FAB anchored to the right edge. Collapsed by
  // default to a thin pill showing just the icon; expands on hover/click
  // to reveal the "Autofill" label. Clicking the expanded FAB triggers
  // smart fill. The user can collapse it by clicking the chevron or it
  // auto-collapses after 4s of no interaction. Remembers collapsed state
  // across pages via chrome.storage.local.
  //
  // Skip the FAB on the AutoApply app itself (it's our own UI) and on
  // pages that can't contain forms (new-tab, extensions, etc.).

  function injectFAB() {
    if (isAppPage) return;
    if (location.protocol === "chrome-extension:" || location.protocol === "chrome:") return;

    const existing = document.getElementById("__autoapply_fab");
    if (existing) existing.remove();

    // ── Outer wrapper (fixed, right edge) ──
    const fab = document.createElement("div");
    fab.id = "__autoapply_fab";
    Object.assign(fab.style, {
      position: "fixed",
      top: "50%",
      right: "0",
      transform: "translateY(-50%)",
      zIndex: "2147483646",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      fontSize: "13px",
      transition: "all 0.3s cubic-bezier(.4,0,.2,1)",
      userSelect: "none",
    });

    // ── Inner button ──
    const btn = document.createElement("div");
    btn.id = "__autoapply_fab_btn";
    Object.assign(btn.style, {
      display: "flex",
      alignItems: "center",
      gap: "6px",
      background: "linear-gradient(135deg, #6366f1, #8b5cf6)",
      color: "#fff",
      borderRadius: "12px 0 0 12px",
      padding: "10px 10px 10px 12px",
      cursor: "pointer",
      boxShadow: "0 4px 16px rgba(99,102,241,.35)",
      whiteSpace: "nowrap",
      overflow: "hidden",
      transition: "all 0.3s cubic-bezier(.4,0,.2,1)",
    });

    // Icon (lightning bolt SVG)
    const icon = document.createElement("span");
    icon.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`;
    icon.style.flexShrink = "0";
    icon.style.display = "flex";
    icon.style.alignItems = "center";

    // Label
    const label = document.createElement("span");
    label.textContent = "AutoApply";
    label.style.fontWeight = "600";
    label.style.fontSize = "13px";
    label.style.transition = "opacity 0.2s, width 0.3s, margin 0.3s";
    label.style.overflow = "hidden";

    // Collapse chevron
    const chevron = document.createElement("span");
    chevron.innerHTML = "›";
    chevron.style.fontSize = "16px";
    chevron.style.fontWeight = "bold";
    chevron.style.transition = "transform 0.3s";
    chevron.style.cursor = "pointer";
    chevron.style.flexShrink = "0";
    chevron.style.padding = "0 2px";

    btn.appendChild(icon);
    btn.appendChild(label);
    btn.appendChild(chevron);
    fab.appendChild(btn);
    document.body.appendChild(fab);

    // ── Collapse / expand logic ──
    let collapsed = true; // start collapsed

    function collapse() {
      collapsed = true;
      label.style.width = "0";
      label.style.opacity = "0";
      label.style.marginRight = "0";
      chevron.style.transform = "rotate(180deg)";
      btn.style.padding = "10px 8px 10px 10px";
      btn.style.borderRadius = "12px 0 0 12px";
      try { chrome.storage.local.set({ __autoapply_fab_collapsed: true }); } catch {}
    }

    function expand() {
      collapsed = true; // will set to false after measuring
      label.style.width = "auto";
      label.style.opacity = "1";
      label.style.marginRight = "2px";
      chevron.style.transform = "rotate(0deg)";
      btn.style.padding = "10px 10px 10px 12px";
      collapsed = false;
      try { chrome.storage.local.set({ __autoapply_fab_collapsed: false }); } catch {}
    }

    // Auto-collapse timer
    let autoCollapseTimer = null;
    function resetAutoCollapse() {
      clearTimeout(autoCollapseTimer);
      autoCollapseTimer = setTimeout(() => {
        if (!collapsed) collapse();
      }, 4000);
    }

    // Restore saved state (default to collapsed)
    try {
      chrome.storage.local.get("__autoapply_fab_collapsed", (res) => {
        if (res.__autoapply_fab_collapsed === false) {
          expand();
          resetAutoCollapse();
        } else {
          collapse();
        }
      });
    } catch { collapse(); }

    // Hover: expand temporarily
    fab.addEventListener("mouseenter", () => {
      if (collapsed) expand();
      clearTimeout(autoCollapseTimer);
    });
    fab.addEventListener("mouseleave", () => {
      resetAutoCollapse();
    });

    // Chevron click: toggle collapse (don't trigger fill)
    chevron.addEventListener("click", (e) => {
      e.stopPropagation();
      if (collapsed) { expand(); resetAutoCollapse(); }
      else collapse();
    });

    // Button click: trigger smart fill
    btn.addEventListener("click", () => {
      if (collapsed) {
        // First click when collapsed = expand
        expand();
        resetAutoCollapse();
        return;
      }
      // Expanded click = trigger fill
      btn.style.opacity = "0.7";
      label.textContent = "Filling…";
      doSmartFill().then((result) => {
        btn.style.opacity = "1";
        label.textContent = "AutoApply";
        if (result.filled > 0) {
          showToast(`Filled ${result.filled} fields${result.aiCount ? ` (${result.aiCount} via AI)` : ""}. Review and submit!`, true);
        } else if (result.error) {
          showToast(result.error, false);
        } else {
          showToast("No empty fields found to fill.", true);
        }
        resetAutoCollapse();
      }).catch(() => {
        btn.style.opacity = "1";
        label.textContent = "AutoApply";
      });
    });

    // Start collapsed
    collapse();
  }

  // Inject FAB once DOM is ready. Guard against edge cases where body is
  // not yet available (very early injection).
  if (document.body) {
    injectFAB();
  } else {
    const bodyObs = new MutationObserver(() => {
      if (document.body) { bodyObs.disconnect(); injectFAB(); }
    });
    bodyObs.observe(document.documentElement, { childList: true });
  }

  function showToast(message, ok) {
    const existing = document.getElementById("__autoapply_toast");
    if (existing) existing.remove();
    const toast = document.createElement("div");
    toast.id = "__autoapply_toast";
    toast.textContent = (ok ? "✅ " : "⚠️ ") + message;
    Object.assign(toast.style, {
      position: "fixed", bottom: "24px", right: "24px", zIndex: "2147483647",
      background: ok ? "#059669" : "#D97706", color: "#fff",
      padding: "12px 18px", borderRadius: "10px", fontSize: "13px",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      maxWidth: "360px", boxShadow: "0 6px 20px rgba(0,0,0,.25)",
    });
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 7000);
  }

  function showUpgradeToast(message) {
    const existing = document.getElementById("__autoapply_upgrade_toast");
    if (existing) existing.remove();
    const toast = document.createElement("div");
    toast.id = "__autoapply_upgrade_toast";
    Object.assign(toast.style, {
      position: "fixed", bottom: "24px", right: "24px", zIndex: "2147483647",
      background: "linear-gradient(135deg, #6366f1, #8b5cf6)", color: "#fff",
      padding: "16px 20px", borderRadius: "14px", fontSize: "13px",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      maxWidth: "380px", boxShadow: "0 8px 28px rgba(99,102,241,.4)",
      lineHeight: "1.5",
    });
    toast.innerHTML = `
      <div style="font-weight:700;font-size:15px;margin-bottom:6px">🚀 Upgrade to Premium</div>
      <div style="margin-bottom:10px">${escapeHtml(message)}</div>
      <div style="display:flex;gap:8px">
        <button id="__aa_upgrade_btn" style="flex:1;padding:8px 14px;background:#fff;color:#6366f1;border:none;border-radius:8px;font-weight:700;font-size:13px;cursor:pointer">₹99/month →</button>
        <button id="__aa_dismiss_btn" style="padding:8px 10px;background:rgba(255,255,255,.15);color:#fff;border:none;border-radius:8px;font-size:12px;cursor:pointer">Later</button>
      </div>
    `;
    document.body.appendChild(toast);
    toast.querySelector("#__aa_upgrade_btn")?.addEventListener("click", () => {
      window.open("https://mango-ocean-0f1de6810.2.azurestaticapps.net/#/upgrade", "_blank");
      toast.remove();
    });
    toast.querySelector("#__aa_dismiss_btn")?.addEventListener("click", () => toast.remove());
    setTimeout(() => toast.remove(), 15000);
  }

  console.log("[AutoApply] content script v1.7.0 loaded on", location.href, "top=", window.top===window.self);
})();
