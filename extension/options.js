/* options.js — AutoApply v1.5 full profile editor + resume upload */

const $ = (s) => document.querySelector(s);
// Same backend as the Flutter web app so resume + saved answers stay in sync.
const DEFAULT_API_BASE = "https://autoapplynow.in";
const LEGACY_API_BASE = "https://autoapplynow.in";

let API_BASE = DEFAULT_API_BASE;
let TOKEN = null;
let PROFILE = null;

function setStatus(msg, kind) {
  const el = $("#status");
  if (!msg) { el.style.display = "none"; return; }
  el.textContent = msg;
  el.className = "status " + (kind || "info");
  el.style.display = "block";
  if (kind === "ok") setTimeout(() => { el.style.display = "none"; }, 3000);
}

function normalizeApiBase(url) {
  return (url || DEFAULT_API_BASE).trim().replace(/\/+$/, "") || DEFAULT_API_BASE;
}

async function loadConfig() {
  return new Promise((res) => {
    chrome.storage.local.get(["autoapply_api_base", "autoapply_token"], (r) => {
      const stored = r.autoapply_api_base;
      if (!stored || stored === LEGACY_API_BASE) {
        chrome.storage.local.set({ autoapply_api_base: DEFAULT_API_BASE });
        API_BASE = DEFAULT_API_BASE;
      } else {
        API_BASE = normalizeApiBase(stored);
      }
      TOKEN = r.autoapply_token || null;
      res();
    });
  });
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (TOKEN) headers["Authorization"] = `Bearer ${TOKEN}`;
  const result = await new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      {
        type: "API_REQUEST",
        path,
        opts: { method: opts.method, headers, body: opts.body, timeoutMs: opts.timeoutMs },
      },
      (resp) => {
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else resolve(resp);
      }
    );
  });
  if (!result?.ok) throw new Error(result?.error || "Request failed");
  return result.data;
}

(async function init() {
  await loadConfig();
  if (!TOKEN) {
    $("#signedOutBlock").style.display = "block";
    return;
  }
  $("#mainContent").style.display = "block";
  await loadProfile();
})();

async function loadProfile() {
  setStatus("Loading profile…", "info");
  try {
    const p = await api("/api/v1/profile");
    PROFILE = p;
    populateForm(p);
    renderCustomAnswers(p);
    await loadCompleteness();
    setStatus("", null);
  } catch (e) {
    setStatus("✗ " + e.message, "err");
  }
}

async function loadCompleteness() {
  try {
    const mi = await api("/api/v1/profile/missing-info");
    const total = mi.totalCommon ?? 0;
    const missing = (mi.missing || []);
    const filled = Math.max(0, total - missing.length);
    const customCount = Object.keys((PROFILE && PROFILE.applicationDetails && PROFILE.applicationDetails.customAnswers) || {}).length;
    let txt = `${filled} of ${total} required fields saved`;
    if (missing.length > 0) txt += ` · ${missing.length} still missing`;
    if (customCount > 0) txt += ` · ${customCount} custom answer${customCount === 1 ? "" : "s"} learned`;
    $("#completenessText").textContent = txt;
    // Highlight the missing inputs so the user can see exactly which to fill.
    document.querySelectorAll(".group.field-missing").forEach((el) => el.classList.remove("field-missing"));
    for (const m of missing) {
      const input = document.getElementById(m.key);
      if (input && input.closest(".group")) {
        input.closest(".group").classList.add("field-missing");
      }
    }
  } catch (_) {}
}

function renderCustomAnswers(p) {
  const card = document.getElementById("customAnswersCard");
  const list = document.getElementById("customAnswersList");
  if (!card || !list) return;
  const custom = (p && p.applicationDetails && p.applicationDetails.customAnswers) || {};
  const entries = Object.entries(custom);
  if (entries.length === 0) {
    card.style.display = "none";
    return;
  }
  card.style.display = "block";
  list.innerHTML = "";
  entries.sort((a, b) => (a[1].label || a[0]).localeCompare(b[1].label || b[0]));
  for (const [key, entry] of entries) {
    const label = (entry && entry.label) || key;
    const value = (entry && entry.value) || "";
    const row = document.createElement("div");
    row.className = "group full";
    row.style.borderBottom = "1px solid #F3F4F6";
    row.style.paddingBottom = "10px";
    row.innerHTML = `
      <label style="display:flex;justify-content:space-between;align-items:center">
        <span>${escapeHtml(label)}</span>
        <button type="button" data-delkey="${escapeHtml(key)}" style="background:none;border:none;color:#B91C1C;cursor:pointer;font-size:12px">Remove</button>
      </label>
      <textarea data-cakey="${escapeHtml(key)}" rows="2" style="resize:vertical">${escapeHtml(value)}</textarea>
    `;
    list.appendChild(row);
  }
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-delkey]");
  if (!btn) return;
  const key = btn.getAttribute("data-delkey");
  if (!key) return;
  if (!confirm("Remove this saved answer?")) return;
  try {
    const custom = (PROFILE && PROFILE.applicationDetails && PROFILE.applicationDetails.customAnswers) || {};
    delete custom[key];
    await api("/api/v1/profile/application-details", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ customAnswers: custom }),
    });
    PROFILE.applicationDetails.customAnswers = custom;
    renderCustomAnswers(PROFILE);
    await loadCompleteness();
    setStatus("✓ Saved answer removed.", "ok");
  } catch (err) {
    setStatus("✗ " + err.message, "err");
  }
});

function populateForm(p) {
  const personal = p.personal || {};
  const det = p.applicationDetails || {};
  const name = personal.fullName || p.fullName || personal.firstName || p.email || "User";
  $("#userNameHeader").textContent = name;

  $("#firstName").value = personal.firstName || "";
  $("#lastName").value = personal.lastName || "";
  $("#email").value = p.email || personal.email || "";
  $("#phone").value = personal.phone || "";
  $("#linkedinUrl").value = p.linkedinUrl || personal.linkedinUrl || "";
  $("#githubUrl").value = personal.githubUrl || "";
  $("#portfolioUrl").value = personal.portfolioUrl || "";

  $("#address").value = det.address || "";
  $("#city").value = det.city || "";
  $("#state").value = det.state || "";
  $("#zip").value = det.zip || "";
  $("#country").value = det.country || "";

  $("#visaStatus").value = det.visaStatus || "";
  $("#willingToRelocate").value = det.willingToRelocate || "";
  $("#remoteWork").value = det.remoteWork || "";
  $("#salaryExpectation").value = det.salaryExpectation || "";
  $("#noticePeriod").value = det.noticePeriod || "";

  $("#gender").value = det.gender || "";
  $("#veteranStatus").value = det.veteranStatus || "";
  $("#disability").value = det.disability || "";
  $("#ethnicity").value = det.ethnicity || "";
  $("#coverLetter").value = det.coverLetter || "";

  // Resume info
  if (p.resumeFileName) {
    $("#resumeInfo").innerHTML = `📎 <strong>${p.resumeFileName}</strong> — uploaded ${p.resumeUploadedAt ? new Date(p.resumeUploadedAt).toLocaleDateString() : "previously"}`;
  }
}

// ---- Save ----
$("#btnSave").addEventListener("click", async () => {
  $("#btnSave").disabled = true;
  setStatus("Saving…", "info");
  try {
    // Collect any edits to learned custom answers.
    const custom = (PROFILE && PROFILE.applicationDetails && PROFILE.applicationDetails.customAnswers) || {};
    document.querySelectorAll("[data-cakey]").forEach((el) => {
      const key = el.getAttribute("data-cakey");
      if (!key) return;
      const v = el.value.trim();
      if (!v) { delete custom[key]; return; }
      const existing = custom[key] || { label: key };
      custom[key] = { label: existing.label || key, value: v.slice(0, 2000) };
    });
    await api("/api/v1/profile/application-details", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        firstName: $("#firstName").value.trim(),
        lastName: $("#lastName").value.trim(),
        phone: $("#phone").value.trim(),
        linkedinUrl: $("#linkedinUrl").value.trim(),
        githubUrl: $("#githubUrl").value.trim(),
        portfolioUrl: $("#portfolioUrl").value.trim(),
        address: $("#address").value.trim(),
        city: $("#city").value.trim(),
        state: $("#state").value.trim(),
        zip: $("#zip").value.trim(),
        country: $("#country").value.trim(),
        visaStatus: $("#visaStatus").value,
        willingToRelocate: $("#willingToRelocate").value,
        remoteWork: $("#remoteWork").value,
        salaryExpectation: $("#salaryExpectation").value.trim(),
        noticePeriod: $("#noticePeriod").value.trim(),
        gender: $("#gender").value,
        veteranStatus: $("#veteranStatus").value,
        disability: $("#disability").value,
        ethnicity: $("#ethnicity").value,
        coverLetter: $("#coverLetter").value.trim(),
        customAnswers: custom,
      }),
    });
    if (PROFILE.applicationDetails) PROFILE.applicationDetails.customAnswers = custom;
    setStatus("✓ Saved! Autofill will use these values.", "ok");
    await loadCompleteness();
  } catch (e) {
    setStatus("✗ " + e.message, "err");
  } finally {
    $("#btnSave").disabled = false;
  }
});

// ---- Delete account ----
$("#btnDeleteAccount").addEventListener("click", async () => {
  const phrase = window.prompt(
    "This will permanently delete your ApplyRight account, profile, uploaded resumes, and saved answers.\n\n" +
    "Type DELETE (in capitals) to confirm:"
  );
  if (phrase !== "DELETE") {
    setStatus("Account deletion cancelled.", "info");
    return;
  }
  const btn = $("#btnDeleteAccount");
  btn.disabled = true;
  setStatus("Deleting your account…", "info");
  try {
    const resp = await api("/api/v1/account", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: "DELETE" }),
    });
    // Wipe local token regardless of server response
    await new Promise((r) => chrome.storage.local.remove("autoapply_token", r));
    setStatus("✓ Account deleted. You have been signed out.", "ok");
    setTimeout(() => { window.close(); }, 1500);
  } catch (e) {
    btn.disabled = false;
    setStatus("✗ Could not delete account: " + e.message, "err");
  }
});

// ---- Resume upload ----
const drop = $("#resumeDrop");
const fileInput = $("#resumeFile");

drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("dragover"); });
drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("dragover");
  if (e.dataTransfer.files[0]) uploadResume(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) uploadResume(fileInput.files[0]);
});

async function uploadResume(file) {
  if (file.size > 5 * 1024 * 1024) {
    setStatus("✗ File too large (max 5 MB).", "err");
    return;
  }
  setStatus(`Uploading ${file.name}…`, "info");
  $("#resumeInfo").innerHTML = "⏳ Uploading & parsing with AI…";
  try {
    const buf = await file.arrayBuffer();
    // Chunked base64 encoding (avoids call-stack overflow on large files)
    const bytes = new Uint8Array(buf);
    let binary = "";
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    const b64 = btoa(binary);
    const data = await api("/api/v1/profile/resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name, fileBase64: b64 }),
    });
    setStatus(`✓ Resume uploaded — ${data.skillsExtracted || 0} skills extracted.`, "ok");
    $("#resumeInfo").innerHTML = `📎 <strong>${file.name}</strong> — just uploaded`;
    // Reload profile so the auto-extracted fields populate the form
    await loadProfile();
  } catch (e) {
    setStatus("✗ " + e.message, "err");
    $("#resumeInfo").textContent = "";
  }
}
