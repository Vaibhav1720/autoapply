# AutoApply — Quality + Architecture Improvement Plan

**Owner:** Copilot agent (Vibhuti)
**Created:** 11 May 2026
**Goal:** Lift offline regression P@10 from baseline ~0.57 → ~0.70+ AND remove the highest-risk hardcoded constants AND make the system 1k-DAU ready, without breaking the 56-test offline suite.

> **GROUND RULE — DO NOT VIOLATE:**
> After EVERY phase below, run `pytest tests/test_regression_quality.py tests/test_search_intent.py` (offline). If anything fails, **revert that phase's changes** before continuing. Never combine two phases into one commit.

---

## Phase index & order

| # | Phase | Risk | Touches deploy? | Status |
|---|---|---|---|---|
| 1 | Reranker model swap (`gpt4omini` → `o4mini`) — env only | LOW | App Setting flip | TODO |
| 2 | Externalize all matcher knobs to env vars (no behavior change at default) | LOW | App Settings | TODO |
| 3 | Bump `_LI_CACHE_TTL_S` 600 → 1800 (env-driven) | LOW | App Settings | TODO |
| 4 | Region-aware experience-level mapping via JSON config | MED | Code + JSON file | TODO |
| 5 | Externalize `COMPANIES` registry + ATS-typed routing | MED | Code + JSON file | TODO |
| 6 | Embedding-based discipline tagging w/ token fallback | MED-HIGH | Code, embeddings call per request | TODO |
| 7 | Telemetry: `match_events` Cosmos container + write hook | LOW | Bicep + code | TODO |
| 8 | 1k-DAU infra Bicep updates (Flex Consumption, Redis, larger LLM capacity, App Insights sampling) | HIGH | Bicep + redeploy | TODO (last) |

---

## Per-phase detail

### Phase 1 — Swap reranker to `o4mini`

**Why:** `gpt-4o-mini` plateaus at 47-53 score on borderline mismatches → our gate of 50 misclassifies them. `o4-mini` is already deployed at the same capacity (50 GlobalStandard) per [main.bicep#L279](../infra/main.bicep#L279) and reasons better on the rerank task. Code default in [_runtime.py#L34](../api/services/_runtime.py#L34) is already `o4mini` — only the prod App Setting overrides it.

**Files:**
- No code change.
- `az functionapp config appsettings set -g <your-resource-group> -n <your-function-app> --settings AI_RERANK_MODEL=o4mini`

**Verification:**
1. Re-run offline regression with `AI_RERANK_MODEL=o4mini` env var locally:
   ```pwsh
   $env:AI_RERANK_MODEL = "o4mini"
   $env:PYTHONPATH = "<repo-root>\api"
   & <repo-root>\api\.venv311\Scripts\python.exe -u -m pytest <repo-root>\api\tests\test_regression_quality.py <repo-root>\api\tests\test_search_intent.py -c <repo-root>\api\pytest.ini -v --rootdir=<repo-root>\api
   ```
   Expected: 56 PASS. (NOTE: regression suite uses snapshots, so the model swap doesn't change results unless the snapshot was captured with this model. Snapshot tests exist to guard against accidental regressions, not measure absolute lift. The actual lift is observed in production via live discover calls — see Phase 7 telemetry.)
2. Live smoke after deploy: `curl POST /api/v1/jobs/discover` for a known good profile and confirm top-10 contains zero obviously-wrong jobs (manual eyeball, ~3 min).

**Rollback:** `az functionapp config appsettings set ... --settings AI_RERANK_MODEL=gpt4omini`

---

### Phase 2 — Externalize matcher knobs

**Why:** Today the score blend weights, rerank gates, and recency curve are baked into [career_scraper.py#L3325](../api/shared/career_scraper.py#L3325) and [routes.py#L857, L1081](../api/services/jobs/routes.py#L857). Pulling them into env vars lets us A/B without redeploy.

**Knobs to expose (all with current value as default — pure refactor):**

| Env var | Default | Replaces |
|---|---|---|
| `MATCH_W_SKILL` | `0.18` | hardcoded `skill*0.18` |
| `MATCH_W_TITLE` | `0.20` | hardcoded `title*0.20` |
| `MATCH_W_LOC` | `0.15` | hardcoded `loc*0.15` |
| `MATCH_W_EXP` | `0.32` | hardcoded `exp_score*0.32` |
| `MATCH_W_REC` | `0.15` | hardcoded `rec*0.15` |
| `MATCH_DISC_PENALTY` | `0.6` | hardcoded `skill * 0.6` |
| `RERANK_GATE_PRIMARY` | `50` | hardcoded `>= 50` (regression_harness) |
| `RERANK_GATE_SECONDARY` | `35` | hardcoded `>= 35` (routes.py L1081) |
| `RERANK_GATE_LOOSE` | `20` | hardcoded `>= 20` (routes.py L857) |
| `LI_CACHE_TTL_S` | `1800` (was 600) | literal in career_scraper.py |
| `LI_CACHE_MAX_ENTRIES` | `200` | literal in career_scraper.py |

**Implementation pattern (one place, top of `career_scraper.py` after `_LI_BULK_MAX`):**
```python
def _f(name: str, default: float) -> float:
    try: return float(os.environ.get(name, default))
    except (TypeError, ValueError): return default

def _i(name: str, default: int) -> int:
    try: return int(os.environ.get(name, default))
    except (TypeError, ValueError): return default

_W_SKILL = _f("MATCH_W_SKILL", 0.18)
_W_TITLE = _f("MATCH_W_TITLE", 0.20)
_W_LOC   = _f("MATCH_W_LOC",   0.15)
_W_EXP   = _f("MATCH_W_EXP",   0.32)
_W_REC   = _f("MATCH_W_REC",   0.15)
_DISC_PENALTY = _f("MATCH_DISC_PENALTY", 0.6)
_LI_CACHE_TTL_S = _i("LI_CACHE_TTL_S", 1800)
_LI_CACHE_MAX = _i("LI_CACHE_MAX_ENTRIES", 200)
```

Then replace literals with these names. For rerank gates in `routes.py` and `regression_harness.py`, add the helpers there too.

**Verification:**
1. Offline 56-test suite still passes with NO env vars set (defaults must reproduce today's behavior bit-for-bit — sums to 1.00 same as today: 0.18+0.20+0.15+0.32+0.15 = 1.00 ✓).
2. With `MATCH_W_EXP=0.50` set, expect at least one snapshot to numerically shift; revert to confirm.

**Rollback:** Single git revert; defaults preserve today's exact behavior.

---

### Phase 3 — Bump LinkedIn cache TTL 600 → 1800

**Why:** 10-min cache thrashes with concurrent users (fewer cache hits → more LinkedIn 429s). 30 min is acceptable staleness for job discovery (jobs don't go stale that fast).

**Implementation:** Already covered by Phase 2 (default changed to 1800 in the env-helper). No additional code change.

**Verification:** Phase-2 test pass is sufficient. Live: monitor `_LI_CACHE` hit rate via App Insights custom metric (Phase 7).

**Rollback:** `LI_CACHE_TTL_S=600` env var.

---

### Phase 4 — Region-aware experience-level mapping

**Why:** Today's `_LEVEL_MIN_YEARS` ([career_scraper.py#L2754](../api/shared/career_scraper.py#L2754)) hardcodes US/FAANG leveling (5y=Senior, 8y=Staff). India "Senior @ 3y" is hard-dropped. Need region-aware overrides.

**Implementation:**
1. Create `api/shared/data/level_mappings.json`:
   ```json
   {
     "default": {"intern":0,"junior":0,"mid":3,"senior":5,"lead":5,"staff":8,"principal":10,"architect":8,"distinguished":12,"fellow":15,"director":10,"vp":12,"head":10,"manager":4,"associate":1},
     "IN":      {"senior":2,"lead":4,"staff":6,"principal":8,"architect":6,"director":8,"vp":10,"manager":3},
     "EU":      {"senior":4,"lead":5,"staff":7,"principal":9,"director":9,"vp":11},
     "US":      {}
   }
   ```
2. Load on module init into `_LEVEL_MIN_YEARS_BY_REGION: dict[str, dict[str,int]]`.
3. Add `_user_region_from_locs(user_locs) -> str` (map Bangalore/Mumbai/etc → "IN", London/Berlin/etc → "EU", else "US"). Reuse `_CITY_TO_COUNTRY`.
4. In score loop, build `level_min = {**default, **region_overrides}` once per profile.
5. Same for `_NUM_LEVEL_MIN_YEARS` (numbered titles "II/III/IV") — keep simpler since these track ATSes not regions.

**Verification:**
1. Offline 56-test pass (default region = "default" so nothing changes for existing snapshots).
2. New unit test `test_level_mapping_region.py`:
   - US profile, "Senior Engineer" with 3y → exp_score < 60 (today's behavior).
   - IN profile, "Senior Engineer" with 3y → exp_score >= 80 (new behavior).
3. Add to memory note: "Phase 4 shipped — verify with India Test Profile #3 in next live run."

**Rollback:** Delete `level_mappings.json` (code falls back to "default" only) OR `LEVEL_MAPPINGS_DISABLE=1` env flag.

---

### Phase 5 — Externalize COMPANIES registry

**Why:** 120+ companies in code = redeploy per addition. ATS URL change = silent breakage. The `companies` Cosmos container already exists per [main.bicep#L82](../infra/main.bicep#L82); we should use it.

**Implementation:**
1. Extract `COMPANIES` dict from [career_scraper.py#L70](../api/shared/career_scraper.py#L70) into `api/shared/data/companies.json`. Each entry MUST include a new `ats` field: `"greenhouse"|"lever"|"ashby"|"workable"|"smartrecruiters"|"icims"|"workday"|"linkedin"|"custom"`.
2. Module init: load `companies.json` → in-memory `COMPANIES` dict. Keep cache-on-first-use semantics so unit tests don't re-read the file.
3. Refactor `_API_SCRAPERS` ([career_scraper.py#L2149](../api/shared/career_scraper.py#L2149)):
   - For `ats` ∈ {greenhouse, lever, ashby, workable, smartrecruiters}, route to `_api_<ats>_generic(board, query, location)` based on a `board` field in companies.json.
   - For `ats: custom` → keep the bespoke function (Amazon, Uber, Microsoft, Apple) but look it up by `companyId` in a small `_CUSTOM_SCRAPERS` map.
4. Bonus: admin endpoint `POST /api/v1/admin/companies` that writes to Cosmos `companies` container (or to a backing JSON for now). Out of scope for this round — TODO.

**Verification:**
1. Offline 56-test pass (companies.json must exactly mirror today's COMPANIES).
2. Diff: `python -c "from shared import career_scraper as a, json; print(json.dumps(a.COMPANIES, sort_keys=True))"` before/after must be identical except for the new `ats` field.
3. Live smoke: discover for `comp-stripe` (greenhouse) and `comp-amazon` (custom) both return jobs.

**Rollback:** Keep the `COMPANIES = {...}` dict in code commented out at the bottom of `career_scraper.py` for the first deploy; remove in a follow-up.

---

### Phase 6 — Embedding-based discipline tagging ✅ DONE 2026-05-11 (opt-in shadow mode)

**Why:** `_DISCIPLINE_TITLE_TOKENS` misses generic phrases ("Member of Technical Staff", "Software Specialist II"), non-English titles, and new frameworks. Embedding similarity generalizes.

**Implementation (as deployed, conservative shadow mode):**
1. New JSON anchor file [api/shared/data/discipline_anchors.json](../api/shared/data/discipline_anchors.json) — one short prototypical sentence per discipline (25 disciplines covered).
2. New module [api/shared/discipline_embeddings.py](../api/shared/discipline_embeddings.py):
   - Lazy + thread-safe anchor cache (embedded ONCE per process via `text-embedding-3-small`, 1536-dim, cheap).
   - `disciplines_for_text(text)` returns `set[str]` of disciplines whose cosine ≥ `DISCIPLINE_EMBED_THRESHOLD` (default 0.55).
   - Returns `set()` on any failure — soft hint only.
3. Extended [api/shared/embeddings.py](../api/shared/embeddings.py) with optional `model=` kwarg on `generate_embedding` / `generate_embeddings_batch` (additive, backward-compatible).
4. Hooked into `_job_conflicts_discipline` inside `match_jobs_to_profile` ([career_scraper.py](../api/shared/career_scraper.py)) as a FALLBACK only when keyword tokens find nothing — keeps cost and latency predictable.
5. **OFF BY DEFAULT in prod.** Enable per-cohort with `DISCIPLINE_EMBED_ENABLE=1` (also tunable: `DISCIPLINE_EMBED_THRESHOLD`).

**Verification:** ✅ Offline 210/210 PASS (11 new). Health 200. Cost guard: feature off in prod, plus ₹500 daily budget alarm catches accidental embed-call spike.

**Rollback:** Unset `DISCIPLINE_EMBED_ENABLE` (or set to `0`).

---

### Phase 7 — Telemetry: `match_events` Cosmos container ✅ DONE 2026-05-11

**Why:** Zero feedback loop today. Every accuracy improvement is anecdotal. Logging `(jobId, score, rerankScore, matched)` enables future learned weights.

**Implementation (as deployed):**
1. Bicep: added explicit `match_events` container resource to [main.bicep](../infra/main.bicep) — partition key `/userId`, autoscale 1000 RU/s shared, `defaultTtl: 2592000` (30-day auto-purge), narrow indexing policy (only `/userId`, `/companyId`, `/timestamp`, `/searchId`).
2. Container also created live via `az cosmosdb sql container create` (idempotent with Bicep).
3. New module `api/shared/telemetry.py` exposes `record(...)` — fire-and-forget, never raises. Pure builder `build_event(...)` is unit-testable.
4. Hooked into `discover_company_jobs` (zero-match + success paths) and bulk `_discover_one` (success + error paths) in [routes.py](../api/services/jobs/routes.py).
5. Captures: top-10 jobIds + scores, rerank model, env-tunable weight snapshot, scrape/filter/match counts, durationMs, region, searchId.

**Verification:** ✅ Offline 199/199 PASS (10 new telemetry tests). Health 200. Disable flag: `MATCH_EVENTS_DISABLE=1`. Live verification query:
```bash
az cosmosdb sql query -g <your-resource-group> -a <your-cosmos-account> -d autoapply -c match_events --query-text "SELECT TOP 5 * FROM c ORDER BY c.timestamp DESC"
```

---

### Phase 8 — 1k-DAU infra (LAST, biggest blast radius)

> **Ops note (2026-05):** Do **not** run Consumption + Flex Function Apps in parallel.
> Prod stays on `autoapply-func-dev` (Y1) until a deliberate cutover. `autoapply-func-flex-dev`
> was removed — dual apps duplicated harvest/prewarm timers (~2× background cost).
> Background harvest timer is **paused** (`HARVEST_ENABLED=0`); re-enable when traffic
> justifies it. Harvest should later read `match_events` `discover_run` + `jobs` scrape_cache
> (user queries are already stored there) instead of a static query×location grid.

**Why:** Current infra has 4 hard blockers at ~200-300 DAU (Y1 cold starts, in-process caches, Cosmos RU on telemetry, LLM TPM). Total cost: ~$180-240/mo over today.

**Implementation (Bicep diffs in `infra/main.bicep`):**

1. **Hosting plan: Y1 → Flex Consumption (FC1)** — **migrate prod once, then delete Y1:**
   ```bicep
   resource hostingPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
     name: '${functionAppName}-plan'
     location: location
     sku: { name: 'FC1', tier: 'FlexConsumption' }
     kind: 'functionapp'
     properties: { reserved: true }
   }
   ```
   ~$80/mo, eliminates cold starts, 60-min execution cap.

2. **Add Azure Redis (Basic C0):**
   ```bicep
   resource redis 'Microsoft.Cache/Redis@2023-08-01' = {
     name: 'autoapply-redis-${env}'
     location: location
     properties: { sku: { name: 'Basic', family: 'C', capacity: 0 }, enableNonSslPort: false }
   }
   ```
   App Setting: `REDIS_CONN=<connection_string>`. Migrate `_LI_CACHE` (career_scraper) and `_runtime` LLM-result cache (if any) to Redis behind a small `RedisCache` adapter that falls back to in-process dict if `REDIS_CONN` is unset (preserves local-dev simplicity).

3. **Bump LLM model capacity:**
   ```bicep
   { name: 'o4mini', model: 'o4-mini', version: '2025-04-16', sku: 'GlobalStandard', capacity: 250 }
   ```
   (50 → 250). Costs are pay-per-call so capacity bump only enables higher TPM, doesn't cost more at idle.

4. **Bump userJobMatches RU:**
   ```bicep
   options: { autoscaleSettings: { maxThroughput: 4000 } }   // was 1000
   ```
   ~$30/mo headroom, fixes write saturation at 1k DAU peaks.

5. **App Insights sampling:**
   In function app App Settings: `APPLICATIONINSIGHTS_SAMPLING_PERCENTAGE=25`.

6. **Async discover (prefer Storage Queue on existing storage account):**
   - New worker function; `/api/v1/jobs/discover/bulk` returns 202 + poll.
   - **DEFER if time-tight** — mark as Phase 8b. (Service Bus removed from dev — not required.)

**Verification:**
1. `bicep build infra/main.bicep` passes (no schema errors).
2. `az deployment group what-if -g <your-resource-group> --template-file infra/main.bicep` shows expected diffs (no surprise deletes).
3. Cold-start test: hit `/api/v1/health` after 30 min idle. Today: 3-8 s. After: <500 ms.
4. Load test: simple `k6` 10-VU 5-min smoke against `/discover` should return 200s with no 429 / 5xx spikes.
5. Cost guard: confirm Cosmos RU consumption stays <2000 RU/s sustained per Azure Monitor.

**Rollback:** Each Bicep change is reversible by reverting the file and `az deployment group create` again. Redis can be left provisioned (cheap) but unwired by removing `REDIS_CONN` App Setting.

---

## Universal verification checklist (run after EVERY phase)

```pwsh
$env:AI_RERANK_MODEL = "o4mini"   # current desired model
$env:PYTHONPATH = "<repo-root>\api"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
& <repo-root>\api\.venv311\Scripts\python.exe -u -m pytest `
    <repo-root>\api\tests\test_regression_quality.py `
    <repo-root>\api\tests\test_search_intent.py `
    -c <repo-root>\api\pytest.ini -v `
    --rootdir=<repo-root>\api
```

**Pass criterion:** `56 passed` in <10 s. ANY failure → revert that phase's commit before continuing.

---

## Hallucination guard — facts I must remember

- Existing model deployments (per [main.bicep#L276-281](../infra/main.bicep#L276-L281)): `gpt41`, `gpt4o`, `gpt4omini`, `o4mini`, `text-embedding-3-large`, `text-embedding-3-small`. **Do not** invent new model names.
- Cosmos containers (per main.bicep): `users`, `companies`, `userJobs`, `userJobMatches` (vector, 3072-dim), `userResumes`, `jobs`. New ones I add must follow the same `parent: cosmosDb` pattern.
- `_LI_CACHE_TTL_S` is at career_scraper.py line **1416** (not 1386 as one earlier doc said).
- Score blend literal is at career_scraper.py line **3325**.
- Production App Setting `AI_RERANK_MODEL=gpt4omini` is the ONLY override; code default is already `o4mini` ([_runtime.py#L34](../api/services/_runtime.py#L34)).
- Three rerank gates exist: 50 (regression_harness), 35 (routes L1081), 20 (routes L857). Do not collapse them — they serve different code paths.
- Test count: 56 (28 regression snapshots + 28 search-intent). Any deviation = something broke.

---

## Phase status tracker (update as you go)

- [x] Phase 1 — model swap (App Setting `AI_RERANK_MODEL=o4mini` deployed; 56/56 PASS @ 4.45s)
- [x] Phase 2 — externalize knobs (career_scraper.py + routes.py + regression_harness.py; 56/56 PASS @ 2.60s; deployed)
- [x] Phase 3 — LinkedIn cache TTL bumped 600 → 1800 (folded into Phase 2 default)
- [x] Phase 4 — region-aware levels (`api/shared/data/level_mappings.json` + loader/resolver in career_scraper.py + wired into `match_jobs_to_profile`. Also fixed FP-boundary bug in `tests/test_regression_quality.py` — `0.7 >= 0.8 - 0.1` now uses 2dp rounding to match the printed assertion message. Added `tests/test_level_mapping_region.py` with 15 tests. **71/71 PASS @ 2.19s** offline. Deployed; health endpoint 200 OK at `2.0.0`.)
- [x] Phase 5 — companies registry (`api/shared/data/companies.json` with 150 entries + `ats`/`atsBoard`/`linkedinId` routing fields. New `_overlay_companies_from_json()` in career_scraper.py merges JSON on top of the embedded dict per-field. Kill switch `COMPANIES_REGISTRY_DISABLE=1`. Loader handles missing file, malformed JSON, and missing `companies` key all returning the embedded dict. Added `tests/test_companies_registry.py` with 10 tests. **81/81 PASS @ 3.58s** offline. Deployed; health endpoint 200 OK at `2.0.0`.)
- [x] Phase 6 — discipline embeddings
- [x] Phase 7 — match_events telemetry
- [ ] Phase 8 — 1k-DAU infra
