"""Realistic 50-user concurrent load test against the AutoApply backend.

Simulates 50 users using the app simultaneously for `DURATION_S` seconds.
Each user runs a realistic session pattern:
  - Periodic health pings (every ~30 s, like an app keep-alive)
  - Profile read (1-2 times)
  - Companies list (1-2 times)
  - At most 1 single-company discover (heavy LLM)
  - At most 1 resume suggest-improvements (heavy LLM)

Cost minimisation is built in:
  - DISCOVER_PROBABILITY and RESUME_PROBABILITY cap the heavy paid actions.
  - Each user runs one heavy action max per session.
  - Hard wall-clock cap (DURATION_S).

Usage:
    python -m eval.load_test_50_users
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime
from statistics import mean, median

import httpx

BASE_URL = os.environ.get(
    "LOADTEST_BASE_URL",
    "https://<your-function-app>.azurewebsites.net",
)
TOKEN = os.environ.get("LOADTEST_TOKEN") or sys.exit(
    "ERROR: set LOADTEST_TOKEN env var (admin JWT)"
)
NUM_USERS = int(os.environ.get("LOADTEST_USERS", "50"))
DURATION_S = int(os.environ.get("LOADTEST_DURATION_S", "90"))

# Probability that a given session includes the heavy action (cost guard).
DISCOVER_PROB = float(os.environ.get("LOADTEST_DISCOVER_PROB", "0.6"))   # ~30 of 50
RESUME_PROB = float(os.environ.get("LOADTEST_RESUME_PROB", "0.4"))       # ~20 of 50

# Companies to rotate through for discover (low-cardinality sample of well-
# known companies that always have results).
DISCOVER_COMPANIES = [
    "comp-amazon", "comp-microsoft", "comp-google",
    "comp-meta", "comp-apple", "comp-netflix",
]
DISCOVER_QUERIES = [
    "software engineer", "backend engineer", "data engineer",
    "frontend engineer", "machine learning engineer",
]

RESUME_TARGETS = [
    "Backend Engineer", "Software Engineer", "Senior Software Engineer",
    "Data Engineer", "Frontend Engineer",
]

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Per-endpoint result store: list of (status, duration_ms, ts_unix)
results: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
errors: dict[str, list[str]] = defaultdict(list)


async def do_request(
    client: httpx.AsyncClient,
    name: str,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    timeout: float = 30.0,
) -> int:
    """Time a single HTTP call and record result. Returns status code."""
    t0 = time.perf_counter()
    status = 0
    try:
        resp = await client.request(
            method, path, json=json_body, timeout=timeout, headers=HEADERS,
        )
        status = resp.status_code
        if status >= 400 and len(errors[name]) < 5:
            try:
                errors[name].append(f"{status}: {resp.text[:200]}")
            except Exception:
                errors[name].append(f"{status}: <unreadable>")
    except httpx.TimeoutException:
        status = 0
        if len(errors[name]) < 5:
            errors[name].append("TIMEOUT")
    except Exception as e:
        status = -1
        if len(errors[name]) < 5:
            errors[name].append(f"EXC: {type(e).__name__}: {str(e)[:120]}")
    finally:
        dur_ms = (time.perf_counter() - t0) * 1000.0
        results[name].append((status, dur_ms, time.time()))
    return status


async def user_session(user_idx: int, deadline: float):
    """Realistic per-user loop until deadline."""
    # Per-user HTTP client = realistic browser fingerprint.
    async with httpx.AsyncClient(
        base_url=BASE_URL, http2=False, timeout=120.0,
    ) as client:
        # Page-load burst (what happens when a user opens the app).
        await do_request(client, "health", "GET", "/api/v1/health", timeout=30)
        await asyncio.sleep(random.uniform(0.2, 0.8))
        await do_request(client, "profile", "GET", "/api/v1/profile", timeout=30)
        await asyncio.sleep(random.uniform(0.3, 1.0))
        await do_request(client, "companies", "GET", "/api/v1/companies", timeout=30)

        # Heavy actions — at most one each per session, randomised order.
        heavy = []
        if random.random() < DISCOVER_PROB:
            heavy.append("discover")
        if random.random() < RESUME_PROB:
            heavy.append("resume")
        random.shuffle(heavy)

        for action in heavy:
            if time.time() >= deadline:
                break
            await asyncio.sleep(random.uniform(0.5, 2.0))
            if action == "discover":
                cid = DISCOVER_COMPANIES[user_idx % len(DISCOVER_COMPANIES)]
                q = random.choice(DISCOVER_QUERIES)
                body = {
                    "companyId": cid,
                    "queries": [q],
                    "locations": ["Bangalore", "Remote", "India"],
                }
                await do_request(
                    client, "discover_company", "POST",
                    "/api/v1/jobs/discover/company",
                    json_body=body, timeout=120,
                )
            else:
                target = random.choice(RESUME_TARGETS)
                body = {
                    "targetRole": target,
                    "targetTitles": [target],
                }
                await do_request(
                    client, "resume_suggest", "POST",
                    "/api/v1/resume/suggest-improvements",
                    json_body=body, timeout=120,
                )

        # Light "browsing" loop until deadline — periodic pings + occasional
        # cheap reads, like a user with the app open.
        next_health = time.time() + random.uniform(20, 35)
        while time.time() < deadline:
            await asyncio.sleep(random.uniform(2.0, 5.0))
            if time.time() >= next_health:
                await do_request(client, "health", "GET", "/api/v1/health", timeout=30)
                next_health = time.time() + random.uniform(20, 35)
                continue
            # Light browsing — re-read profile or companies occasionally.
            if random.random() < 0.4:
                await do_request(client, "profile", "GET", "/api/v1/profile", timeout=30)
            elif random.random() < 0.5:
                await do_request(client, "companies", "GET", "/api/v1/companies", timeout=30)


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = int(round((p / 100.0) * (len(s) - 1)))
    return s[max(0, min(k, len(s) - 1))]


def summarise() -> dict:
    out = {}
    for name, recs in results.items():
        if not recs:
            continue
        statuses = [r[0] for r in recs]
        durs = [r[1] for r in recs]
        ok = sum(1 for s in statuses if 200 <= s < 300)
        c429 = sum(1 for s in statuses if s == 429)
        c5xx = sum(1 for s in statuses if 500 <= s < 600)
        c4xx_other = sum(1 for s in statuses if 400 <= s < 500 and s != 429)
        zero = sum(1 for s in statuses if s in (0, -1))
        out[name] = {
            "calls": len(recs),
            "ok": ok,
            "ok_pct": round(100.0 * ok / len(recs), 1),
            "p50_ms": round(percentile(durs, 50), 0),
            "p95_ms": round(percentile(durs, 95), 0),
            "p99_ms": round(percentile(durs, 99), 0),
            "max_ms": round(max(durs), 0),
            "mean_ms": round(mean(durs), 0),
            "429": c429,
            "5xx": c5xx,
            "4xx_other": c4xx_other,
            "timeout_or_exc": zero,
            "sample_errors": errors.get(name, [])[:3],
        }
    return out


async def main():
    print(f"Load test starting:")
    print(f"  Base URL    : {BASE_URL}")
    print(f"  Users       : {NUM_USERS}")
    print(f"  Duration    : {DURATION_S} s")
    print(f"  Discover P  : {DISCOVER_PROB}")
    print(f"  Resume   P  : {RESUME_PROB}")
    print(f"  Started     : {datetime.now().isoformat()}")
    print()

    deadline = time.time() + DURATION_S
    t_start = time.time()
    tasks = [
        asyncio.create_task(user_session(i, deadline)) for i in range(NUM_USERS)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
    wall = time.time() - t_start

    print(f"\nDone in {wall:.1f}s\n")
    summary = summarise()

    # Pretty table
    cols = ["endpoint", "calls", "ok", "ok%", "p50", "p95", "p99", "max", "429", "5xx", "4xx", "tmo"]
    widths = [22, 6, 6, 6, 7, 7, 7, 7, 5, 5, 5, 5]
    line = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    print(line)
    print("-" * len(line))
    for name, s in summary.items():
        row = [
            name, s["calls"], s["ok"], f'{s["ok_pct"]}%',
            f'{s["p50_ms"]:.0f}', f'{s["p95_ms"]:.0f}', f'{s["p99_ms"]:.0f}',
            f'{s["max_ms"]:.0f}', s["429"], s["5xx"], s["4xx_other"],
            s["timeout_or_exc"],
        ]
        print("  ".join(str(v).ljust(w) for v, w in zip(row, widths)))

    print()
    for name, s in summary.items():
        if s["sample_errors"]:
            print(f"\n{name} — sample errors:")
            for e in s["sample_errors"]:
                print(f"  {e}")

    # Persist raw + summary
    ts = int(time.time())
    out_path = os.path.join(os.path.dirname(__file__), f"loadtest_results_{ts}.json")
    payload = {
        "config": {
            "base_url": BASE_URL,
            "num_users": NUM_USERS,
            "duration_s": DURATION_S,
            "discover_prob": DISCOVER_PROB,
            "resume_prob": RESUME_PROB,
            "started": datetime.now().isoformat(),
            "wall_s": round(wall, 1),
        },
        "summary": summary,
        # Raw arrays kept compact
        "raw": {
            name: [{"status": s, "ms": round(d, 1), "ts": round(t, 3)}
                   for s, d, t in recs]
            for name, recs in results.items()
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\nResults saved -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
