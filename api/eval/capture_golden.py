"""Capture a GOLDEN snapshot for offline regression testing.

For each (profile, company) pair this script does the **expensive bits ONCE**:

  1. Scrape raw jobs from the live company career site.
  2. Run the full deterministic matcher (filter + score) to surface a wide
     candidate set (top N by ``matchScore``).
  3. Ask the LLM judge to grade EVERY candidate as GOOD / MAYBE / BAD and
     remember the verdict per ``jobId``.
  4. Compute baseline metrics (P@10, P@5, BAD) on the snapshot's own
     top-10 — these are the numbers the offline regression test must
     stay >= to.

The output (``api/eval/golden/<profile>__<company>.json``) is a frozen
unit-test fixture. The offline regression test loads it, re-runs only
the deterministic matcher (zero network, zero $$$), looks up verdicts
from the saved map, and asserts current metrics meet the baseline.

Re-run this script ONLY when:
  - You ship a deliberate matcher improvement and want to lock in the
    new (higher) bar, OR
  - The scraped job market has drifted enough that the saved raw_jobs
    are stale and you want fresh ground truth.

Usage (from ``api/``)::

    python -m eval.capture_golden                      # all pairs
    python -m eval.capture_golden senior-7y-usa-ml     # only that profile

Optional env knobs:
  AZURE_AI_ENDPOINT, AZURE_AI_KEY  required (judge runs gpt-4.1)
  GOLDEN_DIR                       output dir override
  GOLDEN_JUDGE_WIDTH               how many top-by-matchScore to judge
                                   (default 25 — gives iteration headroom)
  GOLDEN_COMPANIES_LIMIT           companies per profile (default 2)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make sibling packages (`shared`, `eval`, ...) importable when run from
# api/ or from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.career_scraper import (  # noqa: E402
    match_jobs_to_profile,
    scrape_company,
)
from eval.regression_harness import (  # noqa: E402
    PROFILES,
    _companies_for_profile,
    _ensure_env,
    _judge_jobs_with_llm,
)


DEFAULT_DIR = Path(__file__).parent / "golden"


def _golden_path(label: str, company: str, root: Path) -> Path:
    return root / f"{label}__{company}.json"


def _scrape_for_profile(profile: dict, company_id: str) -> list[dict]:
    """Mirror the production fan-out: every keyword × every preferred
    location, then de-dup by job id."""
    queries = profile["preferences"]["keywords"] or ["software engineer"]
    locs = [l for l in (profile["preferences"].get("locations") or [])
            if l and l.lower() not in ("remote", "anywhere")][:3] or [""]
    raw: list[dict] = []
    for q in queries:
        for lq in locs:
            try:
                raw.extend(scrape_company(company_id, query=q, location=lq))
            except Exception as e:  # noqa: BLE001
                print(f"  scrape err q={q!r} loc={lq!r}: {e}")
    seen, uniq = set(), []
    for j in raw:
        jid = j.get("id")
        if jid and jid not in seen:
            seen.add(jid)
            uniq.append(j)
    return uniq


def _capture_one(profile: dict, company_id: str, judge_width: int) -> dict | None:
    label = profile["_label"]
    print(f"\n>>> {label} x {company_id}")
    t0 = time.time()

    raw_jobs = _scrape_for_profile(profile, company_id)
    print(f"  scraped raw={len(raw_jobs)}")

    matched = match_jobs_to_profile(raw_jobs, profile)
    print(f"  matched={len(matched)}")

    # Judge a WIDER set than top-10 so future iterations that surface a
    # slightly different top-10 still have verdicts available.
    candidates = matched[:judge_width]
    verdicts: dict[str, dict] = {}
    if candidates:
        try:
            judged = _judge_jobs_with_llm(profile, candidates) or []
        except Exception as e:  # noqa: BLE001
            print(f"  judge err: {e}")
            judged = []
        for r in judged:
            v = (r.get("v") or "").upper()
            i = r.get("i", 0) - 1
            if 0 <= i < len(candidates) and v in {"GOOD", "MAYBE", "BAD"}:
                jid = candidates[i].get("id")
                if jid:
                    verdicts[jid] = {"v": v, "why": r.get("why", "")}

    # Compute baseline metrics from the snapshot's OWN top-10 ordering.
    top10 = candidates[:10]
    top5 = top10[:5]

    def _bucket(jobs: list[dict]) -> tuple[int, int, int, int]:
        good = maybe = bad = unk = 0
        for j in jobs:
            v = (verdicts.get(j.get("id") or "") or {}).get("v")
            if v == "GOOD":
                good += 1
            elif v == "MAYBE":
                maybe += 1
            elif v == "BAD":
                bad += 1
            else:
                unk += 1
        return good, maybe, bad, unk

    g10, m10, b10, u10 = _bucket(top10)
    g5, m5, b5, u5 = _bucket(top5)
    n10, n5 = len(top10), len(top5)
    p10 = g10 / n10 if n10 else 0.0
    p5 = g5 / n5 if n5 else 0.0

    elapsed = round(time.time() - t0, 1)
    print(f"  judged={len(verdicts)}/{len(candidates)}  "
          f"top10 G={g10} M={m10} B={b10} U={u10}  "
          f"P@10={p10:.2f} P@5={p5:.2f}  ({elapsed}s)")

    return {
        "schemaVersion": 1,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "profile": label,
        "company": company_id,
        "raw_jobs": raw_jobs,
        "candidate_top_ids": [j.get("id") for j in candidates],
        "verdicts": verdicts,
        "baseline": {
            "judge_top_n": n10,
            "p_at_10": round(p10, 4),
            "p_at_5": round(p5, 4),
            "good": g10, "maybe": m10, "bad": b10, "unknown": u10,
            "good5": g5, "bad5": b5, "unknown5": u5,
        },
        "elapsedSeconds": elapsed,
    }


def main() -> None:
    _ensure_env()

    only_label = sys.argv[1] if len(sys.argv) > 1 else None
    companies_limit = int(os.environ.get("GOLDEN_COMPANIES_LIMIT", "2"))
    judge_width = int(os.environ.get("GOLDEN_JUDGE_WIDTH", "25"))
    out_dir = Path(os.environ.get("GOLDEN_DIR", str(DEFAULT_DIR)))
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    skipped: list[str] = []

    for p in PROFILES:
        if only_label and p["_label"] != only_label:
            continue
        for c in _companies_for_profile(p)[:companies_limit]:
            try:
                snap = _capture_one(p, c, judge_width)
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                print(f"  FAIL {p['_label']} x {c}: {e}")
                skipped.append(f"{p['_label']} x {c}")
                continue
            if snap is None:
                skipped.append(f"{p['_label']} x {c}")
                continue
            path = _golden_path(p["_label"], c, out_dir)
            path.write_text(json.dumps(snap, indent=2, ensure_ascii=False))
            written.append(path)
            print(f"  wrote {path.name}")

    print("\n============ GOLDEN CAPTURE COMPLETE ============")
    print(f"  output dir: {out_dir}")
    print(f"  pairs written: {len(written)}")
    if skipped:
        print(f"  skipped: {len(skipped)} ({', '.join(skipped)})")


if __name__ == "__main__":
    main()
