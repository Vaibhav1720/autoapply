"""URL audit — for each registered company, fetch a few jobs and verify
that the returned `url` is a real per-job deep link (NOT the base
career portal). A URL is considered BAD if it equals or is a strict
prefix of the company's `careersUrl`, or if it lacks a job-id-looking
token (digits/uuid).

Run: python -u eval\\url_audit.py [companyId]

Optional first arg restricts to one company (e.g. comp-amazon).
"""
import os
import re
import sys
import time
from urllib.parse import urlparse

# Make api/ importable when run from api/
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shared.career_scraper import COMPANIES, scrape_company  # noqa


JOB_ID_RE = re.compile(r"(\d{4,}|[0-9a-f]{8}-[0-9a-f]{4})", re.I)


def is_per_job_url(url: str, careers_url: str) -> tuple[bool, str]:
    if not url:
        return False, "empty url"
    if url == careers_url or url.rstrip("/") == careers_url.rstrip("/"):
        return False, "equals careers base"
    cu = urlparse(careers_url)
    u = urlparse(url)
    if u.netloc and cu.netloc and u.netloc == cu.netloc and (u.path.rstrip("/") == cu.path.rstrip("/")):
        return False, "same path as careers base"
    if not JOB_ID_RE.search(url):
        # Workday URLs sometimes use slugs only — check for /job/ segment
        if "/job/" not in url.lower() and "/jobs/" not in url.lower() and "/listing/" not in url.lower():
            return False, "no job id token in url"
    return True, "ok"


def audit_company(cid: str, info: dict) -> dict:
    careers_url = info.get("careersUrl", "")
    name = info.get("name", cid)
    t0 = time.time()
    try:
        jobs = scrape_company(cid, query="engineer", location="")[:5]
    except Exception as e:
        return {"cid": cid, "name": name, "ok": False, "err": str(e), "n": 0, "elapsed": time.time()-t0}
    if not jobs:
        return {"cid": cid, "name": name, "ok": False, "err": "no jobs returned", "n": 0, "elapsed": time.time()-t0}
    bads = []
    for j in jobs:
        ok, reason = is_per_job_url(j.get("url",""), careers_url)
        if not ok:
            bads.append((j.get("title","?")[:60], j.get("url",""), reason))
    return {
        "cid": cid, "name": name, "n": len(jobs), "bad": len(bads),
        "ok": not bads, "samples": [(j.get("title",""), j.get("url","")) for j in jobs[:2]],
        "bads": bads, "elapsed": round(time.time()-t0, 1),
    }


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    targets = [(cid, info) for cid, info in COMPANIES.items() if (only is None or cid == only)]
    print(f"Auditing {len(targets)} company scraper(s)...\n")
    summary = []
    for cid, info in targets:
        r = audit_company(cid, info)
        summary.append(r)
        flag = "OK " if r["ok"] else "BAD"
        print(f"[{flag}] {r['cid']:<20} n={r['n']:>2}  bad={r.get('bad','-'):>2}  ({r.get('elapsed','-')}s)")
        if r.get("err"):
            print(f"       err: {r['err']}")
        for t, u in r.get("samples", [])[:1]:
            print(f"       sample: {t[:50]:<50} -> {u[:100]}")
        for t, u, why in r.get("bads", []):
            print(f"       BAD: {t[:50]:<50} -> {u[:100]}  ({why})")
        print()

    n_ok = sum(1 for r in summary if r["ok"])
    print("=" * 60)
    print(f"Summary: {n_ok}/{len(summary)} companies pass per-job URL audit")
    fails = [r for r in summary if not r["ok"]]
    if fails:
        print("\nFailing companies:")
        for r in fails:
            err = r.get("err")
            if err:
                msg = err
            else:
                msg = f"{r.get('bad',0)}/{r['n']} jobs with bad urls"
            print(f"  - {r['cid']}: {msg}")


if __name__ == "__main__":
    main()
