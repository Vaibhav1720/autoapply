# AutoApply — Living Project State

> **Purpose:** Single source of truth for an LLM (or new contributor) to understand the entire system without re-reading the codebase. Updated after every meaningful change.
>
> **Last updated:** 2026-05-06 (after extension v1.5.0 — fully standalone, Web Store-ready: in-popup signup/login, options page with full profile editor + drag-drop resume upload, configurable API base, privacy policy)
> **Owner:** _redacted_
> **Repo root:** `<repo-root>`

---

## 1. Product

**AutoApply** = AI-powered job-search aggregator. Scrapes 19 major company career sites, ranks openings against the user's resume/profile via vector + LLM rerank, and provides a Chrome extension to autofill applications.

**Live deployment:**
- **Backend (Azure Functions):** `https://<your-function-app>.azurewebsites.net` — `<your-function-app>` in `<your-resource-group>` (sub `<subscription-id>`)
- **Frontend (Static Web App):** `https://<your-static-web-app>.azurestaticapps.net` — Flutter web (`auto_apply` package)
- **AI:** Azure AI Foundry `<your-ai-resource>` — deployments `gpt41`, `o4mini`, `text-embedding-3-large` (3072 dims)
- **Cosmos DB:** `<your-cosmos-account>`, database `autoapply`
- **Test account:** `test-admin@example.com` / pwd `<test-password>` → `user-eeee5555` (4yr SDE-II ex-Amazon, India / Bangalore / Hyderabad / Delhi). **Tier set to `admin` in Cosmos** to bypass the 50/day discover quota during testing.

> **Gotcha:** if Discover shows "Failed to scan" / "Daily limit reached" for every company, the user has hit `FREE_TIER_DAILY_DISCOVER_LIMIT` (50/day). Either set `profile.tier='admin'` in the `profiles` Cosmos container, raise the env var on the Function App, or wait for next UTC day. As of v1.4.1 the Discover screen surfaces the actual API error message in the per-company tile instead of the generic "Failed to scan".

---

## 2. Repository Structure

```
AutoApply/
├── PROJECT_STATE.md                 # ← THIS FILE (living doc)
├── azure-pipelines.yml
├── api/                             # Azure Functions (Python 3.11, v2 model)
│   ├── function_app.py              # ALL HTTP routes — single file
│   ├── host.json
│   ├── local.settings.json
│   ├── requirements.txt
│   ├── shared/
│   │   ├── auth_v2.py               # JWT signup/login, get_user_id()
│   │   ├── blob_client.py           # Azure Storage helpers
│   │   ├── career_scraper.py        # Job scrapers per company + matching
│   │   ├── cosmos_client.py         # Cosmos CRUD + vector_search()
│   │   ├── embeddings.py            # AI Foundry embeddings + cosine
│   │   ├── exceptions.py            # AppException hierarchy
│   │   └── response_helpers.py      # success/error/created responses
│   └── tests/                       # pytest
├── app/                             # Flutter web frontend
│   └── lib/
│       ├── main.dart, app.dart
│       ├── config/                  # azure_config, constants, routes, theme
│       ├── providers/               # auth_provider, profile_provider
│       ├── screens/
│       │   ├── main_shell.dart
│       │   ├── auth/, companies/, discover/, profile/
│       └── services/                # api_service, auth_service
├── extension/                       # Chrome MV3 extension (autofill)
│   ├── manifest.json, background.js, content.js, popup.html, popup.js
├── infra/
│   └── main.bicep                   # All Azure infra
└── docs/
    ├── DESIGN_DOCUMENT.txt
    └── DESIGN_V2.txt
```

---

## 3. Cosmos DB Schema

**Database:** `autoapply` (1000 RU/s shared throughput, free tier)

| Container | Partition Key | Purpose | Vector index |
|---|---|---|---|
| `users` | `/id` | Auth records (email, hashed pwd, jwt salt) | — |
| `profiles` | `/userId` | Full profile, embedding, preferences | `/profileEmbedding` (3072d, cosine) |
| `companies` | `/industry` | Static company catalog (rarely written) | — |
| `applications` | `/userId` | Application history per user | — |
| `job_results` | `/companyId` | Per-user discover snapshots (transient) | — |
| `jobs` | `/companyId` | **Shared job cache** (planned: hourly scrape, 24h TTL, all users read) | `/jobEmbedding` (3072d) |

---

## 4. HTTP API (all routes under `/api/v1`)

All POST/PUT routes require `Authorization: Bearer <jwt>`. Responses use `success_response` / `error_response` (envelope: `{ "data": ..., "error": null }`).

| Method | Route | Function | Purpose |
|---|---|---|---|
| GET  | `/health` | `health_check` | Liveness probe |
| POST | `/auth/signup` | `auth_signup` | Email+pwd signup → JWT |
| POST | `/auth/login` | `auth_login` | Email+pwd login → JWT |
| GET  | `/profile` | `get_profile` | Read full profile |
| PUT  | `/profile` | `update_profile` | Patch profile fields |
| GET  | `/profile/missing-info` | `get_missing_info` | **NEW v1.4**: returns `{missing:[{key,label,type,options?}], totalCommon, completeness}`. Drives the "Improve Autofill" banner on the Flutter profile screen. Includes firstName/lastName/phone if missing on `personal`. |
| POST | `/profile/resume` | `upload_resume` | Upload PDF/DOCX → AI parse. **v1.4:** parser also extracts firstName/lastName/address/city/state/country (full name, e.g. "India" not "in")/zip/portfolio/summary and auto-populates `personal.{firstName,lastName,githubUrl,portfolioUrl}` + `applicationDetails.{address,city,state,country,zip}` only when those keys are empty. |
| PUT  | `/profile/application-details` | `update_application_details` | Address, visa, salary, cover letter, EEO + (v1.4) firstName/lastName/phone/linkedinUrl/githubUrl/portfolioUrl/remoteWork. |
| GET  | `/companies` | `list_companies` | List 19 supported companies |
| POST | `/companies/select` | `select_companies` | Save user's company picks |
| GET  | `/companies/selected` | `get_selected_companies` | Read user's selected companies |
| POST | `/jobs/discover/company` | `discover_company_jobs` | Discover + rank for one company |
| POST | `/jobs/discover/bulk` | `discover_bulk` | Parallel discover for all selected |
| GET  | `/jobs/results` | `get_job_results` | Cached discover snapshot |
| GET  | `/autofill/profile` | `autofill_profile` | Compact profile dict for extension. **v1.2:** `country` is expanded to full name + `countryCode` is also returned. |
| POST | `/autofill/suggest` | `autofill_suggest` | Per-field suggestion. **v1.3:** returns `[{label, value, confidence (0-1), reasoning}]`. Pipeline: (1) memory hit on `customAnswers[normalizedLabel]` (conf 0.99) → (2) `HEURISTIC_MAP` regex on profile fields (conf 0.95) → (3) cover-letter / summary heuristics (conf 0.7–0.85) → (4) `_ai_suggest_fields` with hardened prompt (never invents, returns `''` if unknown, full country names, exact `Yes`/`No` only when profile clearly supports). |
| POST | `/autofill/save-answers` | `autofill_save_answers` | **NEW v1.3**: persists user-typed values from the ask-popup into `profile.applicationDetails.customAnswers[_normalize_label(label)] = {label, value}`. Max 50 per request. Powers the "answer once, never asked again" memory loop. |
| POST | `/resume/review` | `request_resume_review` | ₹499/$19 paid AI critique + human reviewer queue (payment stub) |
| POST | `/admin/reports/skills` | `admin_skills_report` | B2B anonymized skills aggregate (requires `X-Admin-Token`) |

---

## 5. AI/LLM Pipeline (current, will change with optimizations)

**Per `/jobs/discover/company` call (post-optimization):**

1. Read `profile` from Cosmos.
2. **Daily quota check** (`_check_daily_quota`) — free tier capped at `FREE_TIER_DAILY_DISCOVER_LIMIT` (default 50/day). `tier in {pro, lifetime, career_plus, admin}` bypasses.
3. Build search queries (user query + top 2 keywords + fallbacks).
4. Parallel-scrape with `ThreadPoolExecutor` across queries.
5. Country/location soft-filter.
6. `match_jobs_to_profile` keyword scoring → take top 30.
7. **Job embedding cache lookup** (`_get_cached_job_embeddings`): batch-read from `jobs` container by `_job_cache_key(companyId, job)`. Misses are batch-embedded and persisted (`_cache_job_embeddings`, 24h TTL).
8. Cosine vs profile embedding → `vectorScore`, blended `aiScore = 0.5·vector + 0.3·match + 0.2·recency`.
9. **`_should_skip_rerank`**: if top-5 vectorScore spread > `RERANK_SKIP_GAP` (default 15) the LLM rerank is skipped entirely.
10. Else `_ai_rerank_top_jobs(top10)` → single batched call to `AI_RERANK_MODEL` (default `gpt4omini`). Auto-selects `max_tokens`+`temperature` for chat models, `max_completion_tokens` for o-series.
11. Final blend: `aiScore = 0.60·aiReason + 0.25·vector + 0.15·keyword`. Sort. Return top 25.

**Cost per request (post-optimization, warm cache):**
- Embedding: 0–5 jobs (cached) → ~$0.0001
- Rerank: 0–1 call to gpt-4o-mini, ~3,000 in + 500 out → ~$0.0008
- **Total ≈ $0.0001 – $0.001** (₹0.01 – 0.08) — **~10× cheaper** than pre-optimization

---

## 6. `career_scraper.py` — Company Coverage

19 companies. URL strategy:

| Strategy | Companies |
|---|---|
| Native API → real direct job URL | amazon, uber, netflix, stripe, jpmorgan |
| HTML scrape → real official URL | google, apple, salesforce, adobe, barclays, citi |
| Native scraper w/ LinkedIn fallback (URL rewritten to official search via `_rewrite_to_official`) | microsoft, meta, goldman, bofa |
| LinkedIn-only w/ rewrite (`_linkedin_only` wrapper) | morgan-stanley, ubs, hsbc, deutsche |

**Key invariant:** No URL returned to the user contains `linkedin.com` — `_rewrite_to_official` swaps any LinkedIn URL with the company's official `searchUrl?q=<title>`. Original is preserved as `linkedinUrl`.

**Key functions:**
- `COMPANIES` (dict): id → `{name, careersUrl, searchUrl, industry, description}`
- `scrape_company(cid, query, location)` → unified entry point; tries native, then AI fallback; returns ≤75 unique jobs
- `match_jobs_to_profile(jobs, profile)` → soft scoring (no false-positive hard filters); only drops egregious experience mismatches
- `_native_or_linkedin(native_fn, ...)` → native first, LinkedIn fallback with URL rewrite
- `_linkedin_only(...)` → LinkedIn-only wrapper with URL rewrite
- `_rewrite_to_official(jobs, cid)` → swap LinkedIn URLs for official search URLs

---

## 7. Frontend (Flutter web)

- **State:** Provider pattern (`auth_provider.dart`, `profile_provider.dart`).
- **Screens:** Auth (login/signup), Profile, Companies (select 19), Discover (per-company tabs), MainShell (nav).
- **Profile screen (v1.4):** on init calls `loadProfile()` then `_loadMissingInfo()` → GET `/profile/missing-info`. If `missingCount > 0`, renders an `_buildAutofillReadinessCard()` between the Application Details tile and the Extension card — colored progress bar + "$missing common questions still empty — fill them once to skip the popup on every job" + tap routes to `/application-details` and re-loads completeness on return.
- **Application Details screen (v1.4):** new "Personal & Contact" section at top with firstName / lastName / phone / LinkedIn URL / GitHub URL / portfolio URL fields, plus a new "Open to fully remote work" dropdown in the Work Authorization section. All persisted via PUT `/profile/application-details`.
- **Extension bridge:** `profile_screen.dart` registers `window.postMessage` listener in `initState`; probes DOM attribute `data-autoapply-ext` set by `extension/content.js`. Falls back to 1.2s timer before reporting "not installed".
- **API base:** configured in `lib/config/azure_config.dart`.

## 8. Chrome Extension (MV3) — v1.5.0 — STANDALONE / Web Store-ready

**Decoupled from the web app.** Users can sign up, upload resume, edit profile, and autofill jobs without ever visiting the Flutter web app. The web app is now optional (marketing + landing only).

### Files
- `manifest.json` — v1.5.0, MV3. **Permissions reduced for store review:**
  - `permissions: [activeTab, storage, scripting]`
  - `host_permissions: [<your-function-app>.azurewebsites.net]` (backend only, always required)
  - `optional_host_permissions: [http://*/*, https://*/*]` (declared optional so Web Store reviewers see we don't pre-claim broad access — content-script `matches` still needs them, but optional declaration helps justification)
  - `content_scripts.matches: [http/https://*/*]`, `all_frames: true`, `run_at: document_idle`
  - `options_page: "options.html"` — enables full profile editor.
- `popup.html` / `popup.js` (v1.5) — **standalone auth UI:**
  - Sign-in tab + Sign-up tab (calls `/api/v1/auth/login` and `/api/v1/auth/signup` directly).
  - On signup success, auto-opens options page so user can upload resume.
  - Signed-in view shows name + email + profile-completeness progress bar (from `/api/v1/profile/missing-info`), then ⚡ Autofill / 🧠 Smart Fill / Edit profile / Sign out.
  - ⚙️ Settings panel: configurable **API Base URL** (persisted in `chrome.storage.local.autoapply_api_base`) — supports self-hosting.
  - Advanced fallback: paste an existing JWT.
  - Footer link to privacy.html on the SWA.
- `options.html` / `options.js` (NEW v1.5) — **full profile editor on the extension options page** (`chrome://extensions` → Details → Extension options, or popup → "Edit profile"):
  - Drag-and-drop **resume upload** (PDF/DOCX, max 5 MB, chunked base64 → POST `/api/v1/profile/resume` with `{filename, fileBase64}`). After upload, form is auto-repopulated with extracted firstName/lastName/address/etc.
  - Sections: Personal & Contact (firstName, lastName, email[disabled], phone, linkedinUrl, githubUrl, portfolioUrl); Address; Work Authorization & Logistics (visa, relocate, remoteWork, salary, notice); EEO (gender, veteran, disability, ethnicity); Default Cover Letter.
  - Single "💾 Save changes" button → PUT `/api/v1/profile/application-details`. Live completeness bar at top.
- `background.js` — service worker. **`API_BASE` is now read from `chrome.storage.local.autoapply_api_base` per-request** via `getApiBase()` (falls back to `DEFAULT_API_BASE = <your-function-app>.azurewebsites.net`). Handlers: `GET_USER_PROFILE`, `SAVE_TOKEN`, `CLEAR_TOKEN`, `SUGGEST_ANSWERS`, `FETCH_PROFILE_FOR_FILL`, `SAVE_CUSTOM_ANSWERS`.
- `content.js` (~570 lines) — unchanged from v1.4 except version log:
  - Sets `data-autoapply-ext` on top-frame `<html>`; receives postMessage `AUTOAPPLY_TOKEN_SYNCED` from the web app (still works for backwards compat).
  - **Floating "⚡ AutoApply" FAB** — in-iframe support via top-frame `data-autoapply-fab="1"`; in-memory `window.__autoapplyFabDismissed` (auto-clears after 60 s).
  - **`doSmartFill()`** — builds `keyToEl Map`, sends fields to `SUGGEST_ANSWERS`. Fills if `confidence >= 0.6` and value non-empty; otherwise pushes to `lowConfidence[]`.
  - **`renderAskPanel(items, keyToEl)`** — side panel top-right (380 px, max-height 80vh, z-index 2147483647) with per-field input + "Save & Fill" → POSTs `SAVE_CUSTOM_ANSWERS`.
  - Toast: "Filled N • M need your input → see panel". MutationObserver-debounced retry at 1.2 s / 3.5 s / 8 s.

### Trigger paths
  1. **Floating FAB** (default) — click runs `doSmartFill()`; high-confidence fields filled silently, low-confidence ones surface in side ask-panel.
  2. **`#__autoapply` URL hash** — set by web-app "Apply with Autofill" button (legacy, still works).
  3. **Toolbar popup** — manual Autofill / Smart Fill buttons.

### Distribution
- **Continued ZIP distribution:** `app/web/autoapply-extension.zip` (23,539 bytes for v1.5.0), served at `/autoapply-extension.zip` from the SWA. Regenerate via `pwsh tools/build_extension_zip.ps1` after editing `extension/`.
- **Privacy policy:** `app/web/privacy.html` → served at `https://<your-static-web-app>.azurestaticapps.net/privacy.html` (linked from popup footer + required for Web Store listing).

### Chrome Web Store publishing checklist
- [x] Standalone auth (signup + login in popup)
- [x] Standalone profile editor (options page)
- [x] Standalone resume upload (drag/drop in options page)
- [x] Configurable backend URL (settings panel → supports self-hosting)
- [x] Privacy policy hosted at public URL
- [x] `optional_host_permissions` declared (justifies broad content-script matches)
- [x] Description rewritten as user-facing ("Sign up, upload your resume, and AutoApply fills job applications…")
- [x] Icons 16/48/128 already present in `extension/icons/`
- [ ] **Take screenshots** (1280×800 or 640×400, at least 1; recommend 3-5) of: popup signup, popup signed-in, options page with resume upload, FAB on a real career site, ask-panel with low-confidence fields. Save to `docs/store-screenshots/`.
- [ ] **Write store listing copy** (description, short description, category = "Productivity").
- [ ] **Justify permissions in store form:**
  - `activeTab` — "Run autofill on the active tab when user clicks AutoApply."
  - `storage` — "Persist auth token + user-configured API base URL locally."
  - `scripting` — "Re-inject content script when popup invoked on tabs that loaded before extension install."
  - `host_permissions: https://<your-function-app>.azurewebsites.net/*` — "Authenticate + sync profile with the AutoApply backend."
  - Content-script `matches: <all_urls>` — "The floating AutoApply button must be available on any career site (Workday, Greenhouse, Lever, Stripe, custom company sites — hostnames are unbounded). The script only reads form-field labels when the user clicks the button; no page content is sent to our servers otherwise."
- [ ] **Pay one-time $5 developer registration fee** at https://chrome.google.com/webstore/devconsole.
- [ ] **Upload zip + submit for review.** Typical review time: 1–7 days.

---

## 9. Active TODOs / Roadmap

### LLM Cost Optimization

- [x] Cache profile embedding in Cosmos
- [x] Switchable rerank model via `AI_RERANK_MODEL` (default `gpt4omini`, was hard-coded `o4mini`)
- [x] Skip rerank when top-5 vector spread > `RERANK_SKIP_GAP` (default 15) — `_should_skip_rerank`
- [x] Cache job embeddings in `jobs` Cosmos container with 24h TTL — `_get_cached_job_embeddings` / `_cache_job_embeddings`
- [x] Daily quota per free user (`FREE_TIER_DAILY_DISCOVER_LIMIT`, default 50) — `_check_daily_quota`, persists `profile.usage`
- [ ] **TTL on `jobs` container** — Bicep currently has no `defaultTtl`; needs `defaultTtl: -1` and per-doc `ttl` works (already set in code), but documents won't expire until container `defaultTtl` is set. **Action: update `infra/main.bicep` jobsContainer with `defaultTtl: -1` and redeploy.**
- [ ] Shared job cache: hourly Timer trigger scrapes all 19 companies → reuse `jobs` container (also caching scraped lists, not just embeddings)
- [ ] Pre-warm popular search results at 03:00 IST (timer trigger)
- [ ] Bulk discover (`/jobs/discover/bulk`) does NOT yet use the new caches — only the per-company endpoint does

### Monetization Features

- [x] **Resume review endpoint** (`POST /api/v1/resume/review`) — AI first-pass via `AI_REVIEW_MODEL` + queues to `applications` container with status `pending_human`. **Stub:** payment verification not implemented; integrate Razorpay/Stripe before charging.
- [x] **B2B skills report endpoint** (`POST /api/v1/admin/reports/skills`) — anonymized aggregate, requires `X-Admin-Token` header matching `ADMIN_API_TOKEN`. Filters by city / exp range. Returns top skills, titles, cities + sample size.
- [ ] **Affiliate links** on job pages where user is missing skills → show Coursera/Udemy/Scaler/InterviewBit link with `?ref=autoapply` and track clicks (10–40% commission per signup). Tracking: new container `affiliate_clicks` with `{userId, jobId, partner, clickedAt, converted}`. Display logic in `discover_company_jobs` after rerank: for top-N jobs, find missing skills vs profile, query a partner-courses lookup, attach `affiliateOffers: [{skill, partner, url}]` to job payload.
- [ ] Frontend UI for resume review purchase flow + status check
- [ ] Admin dashboard for resume review queue (list pending, mark complete)

### Marketing / Growth (no code yet)

- [ ] Landing page + email capture
- [ ] LinkedIn weekly "Top 50 SDE openings" auto-post (uses scraper data)
- [ ] SEO: auto-generate `/jobs/<company>-<role>-<city>` long-tail pages
- [ ] Reddit / Telegram / WhatsApp seeding
- [ ] Pricing page (Free / Pro ₹299 / Career+ ₹999 / Lifetime ₹4,999)

---

## 10. Environment Variables (Function App)

| Var | Default | Purpose |
|---|---|---|
| `COSMOS_ENDPOINT`, `COSMOS_KEY`, `COSMOS_DATABASE` | — | Cosmos auth |
| `AZURE_AI_ENDPOINT`, `AZURE_AI_KEY` (or `OPENAI_ENDPOINT`/`OPENAI_KEY`) | — | AI Foundry |
| `AI_RERANK_MODEL` | `gpt4omini` | Deployment name for discover rerank |
| `AI_PARSE_MODEL` | `gpt41` | Deployment name for resume parsing |
| `AI_REVIEW_MODEL` | `gpt4omini` | Deployment name for resume review critique |
| `RERANK_SKIP_GAP` | `15` | If top-5 vectorScore spread exceeds this, skip rerank entirely |
| `FREE_TIER_DAILY_DISCOVER_LIMIT` | `50` | Daily discover limit for free tier (0 disables) |
| `ADMIN_API_TOKEN` | (empty) | Required header for `/admin/*` endpoints |
| `JWT_SECRET` | — | JWT signing |
| `BLOB_CONN_STR` | — | Storage (resumes, cover letters) |

**ACTION REQUIRED before deploy:**
- ~~Provision a `gpt4omini` deployment in `<your-ai-resource>` Foundry resource~~ ✅ done
- ~~Set `ADMIN_API_TOKEN`~~ ✅ done (see Change Log for token; rotate before launch)

---

## 11. Useful Commands

```powershell
# Deploy function
cd <repo-root>\api
func azure functionapp publish <your-function-app> --python --build remote

# Deploy frontend (Flutter web)
cd <repo-root>\app
flutter build web --release
# Then push to SWA via swa CLI or GitHub Actions

# Test discover endpoint (Python — PowerShell heredocs are broken in this env)
cd <repo-root>\api
python bulk_test.py
```

---

## 12. Recent Verified Behaviour (2026-05-06)

- All 19 companies deployed and tested via parallel `bulk_test.py`. Sample wins:
  - `comp-microsoft` → 25 jobs, all `jobs.careers.microsoft.com`
  - `comp-meta` → 4 jobs, all `metacareers.com`
  - `comp-goldman` → 25, `higher.gs.com`
  - `comp-jpmorgan` → 2, `jpmc.fa.oraclecloud.com` (real direct URLs)
  - `comp-amazon` → 25, `amazon.jobs` (real direct URLs)
  - `comp-morgan-stanley` → 25, all rewritten to official search
  - `comp-hsbc` → 25, all `mycareer.hsbc.com`
  - `comp-ubs` → 11
- LLM rerank verified working (top results show `aiReasoningScore` 80-95).
- Extension button bug fixed (listener moved to `initState`).
- LinkedIn ID extraction regex fixed: `r'-(\d{8,})(?:[/?]|$)'`.

---

## 13. Change Log

### 2026-05-07 — Extension v1.1.1 + LinkedIn deep-link preservation
- **Bug A (extension):** FAB still didn't appear on `stripe.com/jobs/.../apply` for some users. Causes: (a) FAB was skipped inside iframes (Greenhouse/Workday-style nested apply forms had no entry point), (b) the only dismissal was right-click which permanently hid it for the tab session and was easy to trigger accidentally, (c) injection only retried at fixed 1.5s/4s timeouts — forms that hydrated later (Stripe is a slow React SPA) never got a button.
  - `extension/content.js`: rewrote the FAB block. New `shouldRunFabHere()` allows iframe injection when the top frame doesn't already have a FAB (`data-autoapply-fab="1"` attribute on `<html>`). Replaced right-click dismiss with an explicit × close button. Added `MutationObserver` that re-attempts injection whenever the DOM changes (debounced 400ms). Extra timeouts at 1.2s/3.5s/8s for slow SPAs.
  - `extension/manifest.json`: bumped to `1.1.1`. New zip 13229 bytes.
- **Bug B (job URLs):** For LinkedIn-fallback companies (Morgan Stanley, UBS, HSBC, Deutsche, plus Microsoft/Meta/JPMC/Goldman/BofA when their native API failed), `_rewrite_to_official` was overwriting the LinkedIn deep link (which points to the actual posting) with the company's generic search-results URL. Result: clicking a job sent the user to a search page with no clear way to find the role.
  - `api/shared/career_scraper.py::_rewrite_to_official`: now KEEPS the LinkedIn deep link as primary `url` and exposes the official-site search URL as `applyUrl`. `linkedinUrl` and `sourceNote` retained.
- Deployed: API republished via `func azure functionapp publish <your-function-app> --python --build remote` ("Deployment successful"). Flutter web rebuilt + redeployed; new extension zip live.
- **User action:** existing extension installs must be reloaded (`chrome://extensions` → Reload on the AutoApply card) or re-installed from the freshly-downloaded zip to pick up v1.1.1.

### 2026-05-07 — Extension v1.1.0: floating autofill button on third-party sites
- **Bug:** Reported on `stripe.com/jobs/.../apply` — extension installed but no autofill UI appeared. Root cause: `content.js` only auto-triggered fill when URL contained `#__autoapply` (set by the in-app "Apply with Autofill" flow). Direct visits to a career page had no visible entry point unless the user knew to click the toolbar popup.
- `extension/content.js`: added `injectFloatingButton()` — auto-injects a fixed-position "⚡ AutoApply" gradient pill (bottom-right, z-index 2147483646) on any non-app page that has form fields. Click runs `doSmartFill()`. Includes pre-flight token check (`FETCH_PROFILE_FOR_FILL`) with a friendly toast prompting sign-in if no JWT. Right-click dismisses for the session via `sessionStorage.__autoapply_fab_hidden`. Skips iframes and the AutoApply web app itself. Re-checks after 1.5s and 4s for SPA hydration (Stripe, Greenhouse, Lever).
- `extension/content.js`: added `showToast()` — top-right floating toast for user feedback (filled count, AI count, errors).
- `extension/manifest.json`: bumped to `1.1.0`.
- `tools/build_extension_zip.ps1`: regenerated zip → `app/web/autoapply-extension.zip` 12760 bytes.
- Flutter web rebuilt + redeployed to SWA → new zip live at `https://<your-static-web-app>.azurestaticapps.net/autoapply-extension.zip` (HTTP 200, 12760 bytes verified).
- `PROJECT_STATE.md` §8 rewritten to document all three trigger paths.
- **User action:** existing installs must be reloaded (`chrome://extensions` → Reload button on the AutoApply card) or re-installed from the freshly-downloaded zip to pick up v1.1.0.

### 2026-05-06 — LLM-cost optimization + monetization endpoints
- `function_app.py`: added module-level config block (`AI_RERANK_MODEL`, `AI_PARSE_MODEL`, `AI_REVIEW_MODEL`, `RERANK_SKIP_GAP`, `FREE_TIER_DAILY_DISCOVER_LIMIT`, `ADMIN_API_TOKEN`).
- `function_app.py`: helpers `_should_skip_rerank`, `_check_daily_quota`, `_job_cache_key`, `_get_cached_job_embeddings`, `_cache_job_embeddings`.
- `function_app.py`: `discover_company_jobs` now — (a) checks daily quota up-front, (b) reads/writes embeddings via job cache, (c) skips rerank when vector ranking confident, (d) sends rerank to `AI_RERANK_MODEL` with model-aware kwargs.
- `function_app.py`: `_ai_rerank_top_jobs` model is now configurable; logs include the model name.
- `function_app.py`: NEW endpoint `POST /api/v1/resume/review` — AI first-pass + queue to `applications` container.
- `function_app.py`: NEW endpoint `POST /api/v1/admin/reports/skills` — admin-token-gated B2B aggregate.
- `shared/exceptions.py`: imports `RateLimitError`, `AuthorizationError` now used in `function_app.py`.
- `PROJECT_STATE.md`: this file added & first revision.

**Pending follow-ups:**
1. ~~Provision `gpt4omini` deployment in Azure AI Foundry~~ ✅ created `gpt4omini` (gpt-4o-mini, GlobalStandard, 50 cap)
2. ~~Add `defaultTtl: -1` to `jobsContainer` in `infra/main.bicep`~~ ✅ also applied via `az cosmosdb sql container update --ttl -1` (live)
3. ~~Deploy~~ ✅ published 2026-05-06
4. ~~Smoke-test~~ ✅ amazon discover returned 24 jobs, top result `aiReasoningScore=75` from gpt-4o-mini, `vectorScore=52`
5. App settings live: `AI_RERANK_MODEL=gpt4omini`, `AI_REVIEW_MODEL=gpt4omini`, `AI_PARSE_MODEL=gpt41`, `RERANK_SKIP_GAP=15`, `FREE_TIER_DAILY_DISCOVER_LIMIT=50`, `ADMIN_API_TOKEN=8vzhLGCelZipUIyRQjSYMOt516NwumbsDnAB42TE9o7dakgq` (rotate before public launch)
6. ~~Pre-existing bicep error~~ ✅ fixed `serviceBusNamespace.listKeys(...)` → `serviceBusRootAuthRule.listKeys()`. Also pinned per-region locations (`location='centralus'`, `cosmosLocation='eastus'`, `aiLocation='eastus2'`), reconciled Cosmos throughput to per-container autoscale, switched vector-policy `dimensions` to 1536 to match deployed containers, fixed `job_results` partition key to `/userId`, declared all 6 Foundry model deployments, removed redundant Redis interpolation. Full template now deploys cleanly via `az deployment group create`.
7. Implement timer-triggered shared scrape + pre-warm.
8. Implement affiliate-link feature on top jobs.
9. **Vector-policy dim mismatch:** code uses `text-embedding-3-large` (3072 dims) but Cosmos containers were created with 1536-dim policy. App works because we use Python cosine, not Cosmos `VectorDistance`. To enable native vector search later, recreate `profiles` and `jobs` containers with 3072 dims.
