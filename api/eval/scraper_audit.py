"""Per-scraper smoke audit across all 150 companies.

Calls every registered scraper twice with two semantically distinct
queries (`software engineer` and `product designer`) plus a single
location and writes a JSON report listing:

  - did the call succeed (no exception)
  - how many jobs came back
  - first 3 sample titles
  - whether the two queries returned identical results (signals the
    scraper ignores the `query` param -- not always wrong but worth
    flagging)
  - elapsed seconds

Intentionally LIVE (hits real career APIs). Run sparingly, ideally in a
maintenance window, and only when investigating a "company X returned
nothing" complaint. Each company is bounded by `_PER_CALL_TIMEOUT` and
the whole sweep fans out via a thread pool, so the wall-clock is roughly
2-4 minutes total.

Outputs:
    api/eval/scraper_audit_<unix-ts>.json

Usage:
    cd api
    python -m eval.scraper_audit              # all 150 companies
    python -m eval.scraper_audit comp-uber    # one company

The script does NOT touch Cosmos, the LLM, or the regression baseline.
It is only a diagnostic tool for the scraper layer.
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from shared.career_scraper import COMPANIES, _API_SCRAPERS  # noqa: E402

_QUERIES = [
    ("software_engineer", "software engineer"),
    ("product_designer", "product designer"),
]
_LOCATION = ""  # leave blank: many scrapers reject unknown city strings
_PER_CALL_TIMEOUT_S = 30
_MAX_WORKERS = 12


def _audit_one(company_id: str) -> dict[str, Any]:
    company = COMPANIES.get(company_id, {})
    name = company.get("name", company_id)
    fn = _API_SCRAPERS.get(company_id)
    res: dict[str, Any] = {
        "companyId": company_id,
        "company": name,
        "scraper": getattr(fn, "__qualname__", repr(fn)) if fn else None,
        "ok": False,
        "queries": {},
    }
    if fn is None:
        res["error"] = "no scraper registered"
        return res

    for label, q in _QUERIES:
        t0 = time.time()
        sample: list[dict[str, str]] = []
        count = 0
        err: str | None = None
        try:
            jobs = fn(q, _LOCATION) or []
            count = len(jobs)
            for j in jobs[:3]:
                sample.append({
                    "id": str(j.get("id", "")),
                    "title": (j.get("title") or "")[:80],
                    "location": (j.get("location") or "")[:60],
                })
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        res["queries"][label] = {
            "query": q,
            "count": count,
            "elapsed": round(time.time() - t0, 2),
            "sample": sample,
            "error": err,
        }

    # Best-effort signal that the scraper actually used the query: do the
    # first job IDs differ between the two queries? Same-id-set means the
    # scraper probably ignored `query`. Not always a bug (some boards just
    # return everything and rely on the matcher to filter), but worth a flag.
    qres = res["queries"]
    if "software_engineer" in qres and "product_designer" in qres:
        a_ids = [s.get("id") for s in qres["software_engineer"].get("sample") or []]
        b_ids = [s.get("id") for s in qres["product_designer"].get("sample") or []]
        res["honors_query"] = bool(a_ids) and bool(b_ids) and (a_ids != b_ids)

    res["ok"] = all(
        (qres[l].get("error") is None and qres[l].get("count", 0) > 0)
        for l in [k[0] for k in _QUERIES]
    )
    return res


def main(only: str | None = None) -> None:
    targets = [only] if only else list(COMPANIES.keys())
    targets = [t for t in targets if t in COMPANIES]
    if not targets:
        print(f"No matching company id: {only!r}")
        sys.exit(1)

    print(f"[AUDIT] {len(targets)} companies, queries={[q for _, q in _QUERIES]}")
    started = time.time()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(targets))) as pool:
        futs = {pool.submit(_audit_one, cid): cid for cid in targets}
        for fut in as_completed(futs):
            cid = futs[fut]
            try:
                r = fut.result(timeout=_PER_CALL_TIMEOUT_S * 3)
            except Exception as e:  # noqa: BLE001
                r = {"companyId": cid, "ok": False, "error": f"{type(e).__name__}: {e}"}
            ok = "ok " if r.get("ok") else "FAIL"
            qres = r.get("queries", {})
            counts = " | ".join(
                f"{k}:{(qres.get(k) or {}).get('count', '?')}" for k, _ in _QUERIES
            )
            print(f"  [{ok}] {cid:24s} -> {counts}  honors_query={r.get('honors_query')}")
            results.append(r)

    elapsed = round(time.time() - started, 1)
    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, f"scraper_audit_{int(time.time())}.json")
    summary = {
        "auditedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsedSeconds": elapsed,
        "queries": [q for _, q in _QUERIES],
        "totalCompanies": len(targets),
        "passed": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "ignoresQuery": sum(1 for r in results if r.get("honors_query") is False),
        "results": sorted(results, key=lambda r: r.get("companyId", "")),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(
        f"\n[AUDIT] done in {elapsed}s | passed={summary['passed']} "
        f"failed={summary['failed']} ignoresQuery={summary['ignoresQuery']}"
    )
    print(f"[AUDIT] report: {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
