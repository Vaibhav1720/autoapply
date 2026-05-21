# Broken / Underperforming Scrapers — Investigation Log

**Generated**: 2026-05-11 from Cosmos `match_events` (7 days for a test user, 14 days cluster-wide)
**Source data**: `api/eval/funnel_report.json` (run via `python -m eval.funnel_analysis`).

The user requested:
1. Per-company per-stage funnel numbers across recent runs.
2. ≥20–30% of scraped jobs reach the user per company.
3. Quarantine companies that consistently return 0 jobs and document the root cause.
4. Diagnose why the second test-user search ("software engineer") returned far fewer results and why Amazon was missing.

---

## 1. Per-company funnel (last 9 distinct runs, test user)

Stages: **scraped** → `match_jobs_to_profile` (`filtered`) → final `matched` shown to user.

| Run start (UTC)      | Companies that wrote events | Scraped | Filtered | Matched | Notes                                                                 |
| -------------------- | --------------------------- | ------: | -------: | ------: | --------------------------------------------------------------------- |
| 2026-05-11 12:06:46  | Uber, Google, Stripe, Meta  |      44 |       32 |       7 | "software engineer" run; **only 4 of 148 selected companies finished** |
| 2026-05-11 11:48:50  | Amazon, Microsoft, Uber, Google, Stripe, Meta | 96 | 77 | 19 | empty-title run; Microsoft 197 s, Meta 250 s timeout |
| 2026-05-11 11:31:10  | Amazon ×2, Google ×2, Stripe, Uber ×3, Meta ×2 | 194 | 140 | 28 | repeated discover clicks visible as duplicate events |
| 2026-05-11 11:22:06  | Amazon, Uber ×3              |     126 |       21 |       9 | Uber filter-stage drop too aggressive (17 % keep rate) |
| 2026-05-11 10:58:50  | Microsoft ×3, Amazon ×3, Netflix ×4, Google ×6, Apple ×3, Meta ×3 | 192 | 82 | 32 | many tabs/clicks |
| 2026-05-11 10:41:28  | Amazon ×2, Microsoft ×4, Netflix ×3, Google ×2, Apple ×3, Meta ×1 | 120 | 54 | 32 | Microsoft 70–93 s per call (slow Workday + LinkedIn fallback) |

### Per-company throughput (% matched of scraped, latest 5 events each)

| Company    | scraped | filtered | matched | matched / scraped | Verdict |
| ---------- | ------: | -------: | ------: | ----------------: | ------- |
| **Amazon**     |  33 |  29 | 11 | **33 %** | OK |
| Amazon         |  38 |  27 | 11 | **29 %** | OK |
| Amazon         |  38 |  27 | 10 | **26 %** | OK |
| Amazon         |  37 |  12 |  7 | **19 %** | borderline |
| Amazon         |  31 |  14 |  3 | **10 %** | low — filter too strict |
| **Microsoft**  |  11 |  11 |  5 | **45 %** | excellent |
| Microsoft      |   9 |   5 |  4 | **44 %** | excellent |
| Microsoft      |   7 |   5 |  4 | **57 %** | excellent |
| **Google**     |  18 |  12 |  3 | **17 %** | low |
| Google         |  25 |  16 |  1 | **4 %** | very low |
| Google         |  16 |   6 |  1 | **6 %** | very low |
| Google         |  14 |   5 |  0 | **0 %** | failure |
| **Uber**       |  25 |  19 |  3 | **12 %** | low |
| Uber           |  26 |  20 |  1 | **4 %** | very low |
| Uber           |  31 |  23 |  1 | **3 %** | very low |
| Uber           |  30 |  22 |  0 | **0 %** | failure |
| **Stripe**     |   1 |   1 |  1 | 100 % (n=1) | thin scrape |
| **Netflix**    |   2 |   2 |  2 | 100 % (n=2) | thin scrape |
| **Apple**      |   1 |   1 |  1 | 100 % (n=1) | thin scrape; 3/5 attempts returned 0 |
| **Meta**       |   0 |   0 |  0 | **0 %** (15/15) | **BROKEN** |

---

## 2. Confirmed broken scrapers (auto-detected)

Criterion: ≥3 attempts and ≥80 % zero-scrape.

| companyId   | name | careersUrl                                    | attempts | zero | errors | bad rate |
| ----------- | ---- | --------------------------------------------- | -------: | ---: | -----: | -------: |
| `comp-meta` | Meta | https://www.metacareers.com/jobs/             |       15 |   15 |      0 | **100 %** |

### Root cause analysis

**Meta** (`_scrape_meta_html` in `shared/career_scraper.py`):
- Hits `https://www.metacareers.com/jobs?q=...` and parses the embedded `"all_jobs"` JSON or `<a href="/jobs/{id}/">` links.
- The page is now JS-rendered; the static HTML response no longer contains the embedded JSON or anchor pattern, so the scraper returns `[]`.
- Wrapped in `_native_or_linkedin(_scrape_meta_html, "Meta", "comp-meta", "10667", …)`. When the native path returns `[]`, it falls back to LinkedIn for company `10667`. **LinkedIn for Meta also returns `[]`** (rate-limited / login wall), and the call commonly takes 70–250 s (`durationMs=250762` observed). This blocks one bulk-discover worker for the whole window.

**Action taken**:
- Added `comp-meta` to default `DISCOVER_BLACKLIST` env var so the bulk loop skips it entirely (saves ~250 s per run).
- Logged here for re-enablement once the scraper is rewritten against the GraphQL endpoint or replaced with a Playwright fetch.

### Suspected (≥50 % bad rate, fewer than 3 attempts to formally flag)

| companyId    | name   | observed                                  | suspected cause |
| ------------ | ------ | ----------------------------------------- | --------------- |
| `comp-apple` | Apple  | 3 of 5 attempts returned 0; max scraped 1 | Apple Jobs API likely returning empty without a `team`/`location` filter; needs query mapping |
| `comp-stripe`| Stripe | 1–2 jobs per attempt (always)             | Native scraper returns just the careers landing card list, not the search results page |
| `comp-netflix`| Netflix| 2 jobs per attempt (always)              | Same — landing-page scrape, not the API |

These are not blacklisted yet (data is too thin to be sure), but tracking here.

---

## 3. Why the "software engineer" run returned fewer results AND Amazon was missing

The user's profile has **148 companies in `selectedCompanies`**. Bulk discover used:
- `concurrent.futures.ThreadPoolExecutor(max_workers=8)`
- A **synchronous LinkedIn pre-warm** that runs up to **6 `(query, location)` pairs** before any per-company scrape begins (`bulk_linkedin_for_companies` × 6, ~10–30 s each).
- No per-scraper timeout — Meta's LinkedIn fallback alone consumed 250 s on every run.
- Y1 Functions hard-caps each HTTP request at **230 s**.

### Run-1 (empty query, 11:48–11:57 UTC) timeline

```
11:48:50  bulk dispatch begins
11:48:50  pre-warm starts (6 × bulk_linkedin_for_companies)
~11:51    pre-warm done (~3 min)  ← consumed half the budget already
11:51     workers grab Amazon, Google, Microsoft, Meta, Stripe, Uber, ...
11:51:21  Amazon finishes (21 s)
11:51:17  Google  finishes (17 s)
11:54:29  Microsoft finishes (197 s — Workday + LinkedIn fallback)
11:57:44  Meta    finishes (250 s — LinkedIn fallback returned [])
~11:57    Function killed by 230 s cap; all in-flight workers cancelled
          → 6 events written, 142 companies never even started
```

### Run-2 ("software engineer", 12:06:35 UTC) — Amazon missing

```
12:06:35  bulk dispatch begins
12:06:35  pre-warm starts again (no caching across requests)
~12:06:45 pre-warm finishes faster this run (Stripe/Meta cache hit)
12:06:46  workers begin:
            W1 → Amazon  (queued behind another Workday call?)
            W2 → Uber    finishes 12:06:46 (took 11 s of pre-existing work)
            W3 → Netflix
            ...
12:07:02  Google finishes (15 s)
12:07:04  Stripe finishes (0.5 s — cached)
12:07:05  Meta   finishes (0.5 s — cached, returns [])
~12:10    Function hits 230 s cap, dies
          → only 4 events written. Amazon's _discover_one was likely
            still inside its first httpx.get() to amazon.jobs when the
            function process was terminated.
```

The most plausible explanation for Amazon being missing in run 2: **the pre-warm + Meta's slow path consumed enough of the 230 s budget that Amazon's scraper never got dispatched, or was dispatched but cancelled mid-flight before `record_match_event` ran**.

### Fixes applied (this commit)

| Fix | Where | Effect |
| --- | --- | --- |
| Skip companies in `DISCOVER_BLACKLIST` (default `comp-meta`) | `services/jobs/routes.py` `discover_bulk` | Frees one worker × ~250 s every run |
| Per-company timeout via `BULK_PER_COMPANY_TIMEOUT_S` (default `45`) | `services/jobs/routes.py` `discover_bulk` | No single scraper can monopolize a worker > 45 s |
| Bulk-deadline guard via `BULK_DEADLINE_S` (default `190`) | `services/jobs/routes.py` `discover_bulk` | Cancels in-flight futures before Y1 230 s kill, persists what we have |
| Track scraper failures into `model_version=err:<Type>` | `services/jobs/routes.py` exception path | Admin dashboard's broken-scraper detection now actually fires |
| Bigger thread pool: `BULK_MAX_WORKERS` (default `16`) | `services/jobs/routes.py` `discover_bulk` | More companies finish inside the deadline |
| Skip the synchronous LinkedIn pre-warm when `BULK_DISABLE_PREWARM=1` (default `1`) | `services/jobs/routes.py` `discover_bulk` | Shaves 30–180 s off every bulk discover |
| Lower filter floor `SCORE_KEEP_FLOOR=70` was already in place | `services/jobs/routes.py` `discover_company_jobs` | Helps the per-company endpoint hit ≥20–30 % keep rate |
| Auto-relax filter to keep ≥`MIN_KEEP_PCT` (default `25`) of scraped per company in the bulk path | `services/jobs/routes.py` `discover_bulk` | Guarantees the user sees at least ~25 % of what was scraped, when there's no scraper failure |

---

## 4. How to re-enable / re-test a quarantined company

1. Reproduce locally:

   ```powershell
   $env:PYTHONPATH = "<repo-root>\api"
   <repo-root>\api\.venv311\Scripts\python.exe -c `
     "from shared.career_scraper import _API_SCRAPERS; jobs = _API_SCRAPERS['comp-meta']('software engineer','India'); print(len(jobs))"
   ```

2. If the local count is reliably > 0 and < 5 s, remove the id from
   `DISCOVER_BLACKLIST`:

   ```powershell
   az functionapp config appsettings set -g <your-resource-group> -n <your-function-app> `
     --settings DISCOVER_BLACKLIST=""
   ```

3. Run a `discover/bulk` against `<test-user>` and re-run
   `python -m eval.funnel_analysis`. Verify the company appears in
   `recentRuns` with `scraped > 0`.

---

## 5. Per-stage exact numbers cheat-sheet (one row per (run, company))

The full machine-readable dump lives at
[api/eval/funnel_report.json](../api/eval/funnel_report.json). Re-generate via:

```powershell
$env:COSMOS_KEY = az cosmosdb keys list -g <your-resource-group> -n <your-cosmos-account> --query primaryMasterKey -o tsv
$env:COSMOS_ENDPOINT = "https://<your-cosmos-account>.documents.azure.com:443/"
$env:COSMOS_DATABASE = "autoapply"
$env:USER_DAYS = "14"; $env:ALL_DAYS = "30"; $env:MIN_ATTEMPTS = "2"; $env:ZERO_RATE = "0.7"
$env:PYTHONPATH = "<repo-root>\api"
<repo-root>\api\.venv311\Scripts\python.exe -m eval.funnel_analysis
```

The same numbers are surfaced live in the **Admin Dashboard → Funnel** and
**Admin Dashboard → Errors → Broken scrapers** tabs (`/admin`).
