"""Regression harness — measure end-to-end matcher quality across diverse profiles.

For each synthetic profile we:
  1. Pick a real company.
  2. Scrape jobs (live HTTP).
  3. Run the actual `match_jobs_to_profile` filter+score.
  4. Run vector ranking against the cached profile embedding.
  5. Use the gpt-4.1 deployment as an INDEPENDENT REFERENCE JUDGE — for the
     final top-10 ask: "is this a good match for this candidate? yes/no/maybe".
  6. Compute precision@10, judge-disagreement rate, and per-stage drop counts.

Run:  python -m eval.regression_harness   (from api/ folder, with .venv active)
"""

from __future__ import annotations
import os, sys, json, time
from pathlib import Path
from typing import Any

# Make sure imports resolve when run from api/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.career_scraper import scrape_company, match_jobs_to_profile  # noqa: E402
from shared.embeddings import (
    generate_embedding, generate_embeddings_batch, cosine_similarity,
    profile_to_text, job_to_text,
)  # noqa: E402
# Import the production rerank function so the harness measures end-to-end
# quality, not just the vector pipeline.
import function_app  # noqa: E402
from function_app import _ai_rerank_top_jobs  # noqa: E402

# ── Synthetic profiles spanning the user-base we care about ─────────────────
PROFILES: list[dict] = [
    {
        "_label": "junior-1y-india",
        "id": "test-1",
        "skills": {"technical": ["Python", "Django", "PostgreSQL", "REST APIs", "Git"]},
        "experience": [
            {"title": "Software Engineer Intern", "company": "Razorpay",
             "from": "2025-01", "to": "2025-12", "skills": ["Python", "Django"]},
        ],
        "education": [{"degree": "B.Tech CS", "university": "VIT Vellore", "year": "2025"}],
        "preferences": {"experienceYears": 1, "locations": ["Bangalore", "India"],
                        "keywords": ["software engineer", "backend"]},
    },
    {
        "_label": "mid-4y-india-fullstack",
        "id": "test-2",
        "skills": {"technical": ["TypeScript", "React", "Node.js", "AWS", "PostgreSQL", "Docker", "Kubernetes"]},
        "experience": [
            {"title": "Senior Software Developer", "company": "Flipkart",
             "from": "2023-06", "to": "Present", "skills": ["TypeScript", "React", "Node.js"]},
            {"title": "Software Engineer", "company": "Cred",
             "from": "2022-01", "to": "2023-05", "skills": ["Node.js", "AWS"]},
        ],
        "education": [{"degree": "B.E. Computer Science", "university": "BITS Pilani", "year": "2022"}],
        "preferences": {"experienceYears": 4, "locations": ["Bangalore", "Hyderabad"],
                        "keywords": ["software engineer", "full stack"]},
    },
    {
        "_label": "senior-7y-usa-ml",
        "id": "test-3",
        "skills": {"technical": ["Python", "PyTorch", "TensorFlow", "MLOps", "Kubernetes", "AWS", "Spark", "SQL"]},
        "experience": [
            {"title": "Senior ML Engineer", "company": "Stripe",
             "from": "2022-03", "to": "Present", "skills": ["PyTorch", "MLOps"]},
            {"title": "Machine Learning Engineer", "company": "Square",
             "from": "2019-06", "to": "2022-02", "skills": ["TensorFlow", "Spark"]},
            {"title": "Data Scientist", "company": "Uber",
             "from": "2018-01", "to": "2019-05", "skills": ["Python", "SQL"]},
        ],
        "education": [{"degree": "MS Computer Science", "university": "Stanford", "year": "2018"}],
        "preferences": {"experienceYears": 7, "locations": ["San Francisco", "Seattle", "Remote"],
                        "keywords": ["machine learning", "ml engineer", "ai engineer"]},
    },
    {
        "_label": "staff-12y-london-platform",
        "id": "test-4",
        "skills": {"technical": ["Go", "Kubernetes", "Terraform", "AWS", "Distributed Systems", "Kafka", "gRPC", "Postgres"]},
        "experience": [
            {"title": "Staff Software Engineer", "company": "Stripe",
             "from": "2020-01", "to": "Present", "skills": ["Go", "Kubernetes"]},
            {"title": "Senior Software Engineer", "company": "Monzo",
             "from": "2017-03", "to": "2019-12", "skills": ["Go", "AWS"]},
            {"title": "Software Engineer", "company": "Deliveroo",
             "from": "2014-09", "to": "2017-02", "skills": ["Python", "AWS"]},
        ],
        "education": [{"degree": "BSc Computer Science", "university": "Imperial College London", "year": "2014"}],
        "preferences": {"experienceYears": 12, "locations": ["London", "Remote"],
                        "keywords": ["staff engineer", "principal engineer", "platform"]},
    },
    {
        "_label": "newgrad-0y-india-frontend",
        "id": "test-5",
        "skills": {"technical": ["JavaScript", "React", "HTML", "CSS", "Tailwind"]},
        "experience": [],
        "education": [{"degree": "B.Tech IT", "university": "NIT Trichy", "year": "2026"}],
        "preferences": {"experienceYears": 0, "locations": ["Bangalore", "Pune", "Hyderabad"],
                        "keywords": ["software engineer", "frontend developer"]},
    },
    {
        # MBA Finance fresher — Nancy, ITM Business School, intern at UBS GWM.
        # Tests non-engineering profile flow (no software keywords/skills).
        "_label": "mba-finance-fresher-mumbai",
        "id": "test-6",
        "skills": {"technical": ["Microsoft Excel", "Portfolio Analysis",
                                  "Financial Modeling", "Market Research",
                                  "Business Intelligence", "Client Reporting"]},
        "experience": [
            {"title": "Intern, Global Wealth Management", "company": "UBS",
             "from": "2025-06", "to": "Present",
             "skills": ["Excel", "Portfolio Performance Analysis", "Client Reporting"]},
        ],
        "education": [
            {"degree": "MBA Finance", "university": "ITM Business School", "year": "2026"},
            {"degree": "BBA", "university": "IFIM Business School", "year": "2022"},
        ],
        "preferences": {"experienceYears": 0,
                        "locations": ["Mumbai", "Navi Mumbai", "Bangalore"],
                        "keywords": ["finance", "investment banking", "wealth management",
                                      "financial analyst", "business analyst"]},
    },
    # ── Data engineering archetype (BI / ETL / pipelines, distinct from ML) ──
    {
        "_label": "data-engineer-3y-india",
        "id": "test-7",
        "skills": {"technical": ["Python", "SQL", "Apache Spark", "Airflow",
                                  "dbt", "Snowflake", "AWS", "Kafka", "Redshift"]},
        "experience": [
            {"title": "Data Engineer", "company": "Swiggy",
             "from": "2023-04", "to": "Present",
             "skills": ["Python", "Spark", "Airflow", "dbt"]},
            {"title": "Analytics Engineer", "company": "Mu Sigma",
             "from": "2022-02", "to": "2023-03",
             "skills": ["SQL", "Python", "Tableau"]},
        ],
        "education": [{"degree": "B.Tech CSE", "university": "IIIT Hyderabad", "year": "2022"}],
        "preferences": {"experienceYears": 3, "locations": ["Bangalore", "Hyderabad", "Remote"],
                        "keywords": ["data engineer", "analytics engineer", "etl developer"]},
    },
    # ── DevOps / SRE / infra archetype ──
    {
        "_label": "devops-sre-5y-eu-remote",
        "id": "test-8",
        "skills": {"technical": ["Kubernetes", "Terraform", "AWS", "GCP",
                                  "Prometheus", "Grafana", "Helm", "Argo CD",
                                  "GitOps", "Linux", "Bash", "Python"]},
        "experience": [
            {"title": "Site Reliability Engineer", "company": "Cloudflare",
             "from": "2023-01", "to": "Present",
             "skills": ["Kubernetes", "Terraform", "Prometheus"]},
            {"title": "DevOps Engineer", "company": "Zalando",
             "from": "2021-03", "to": "2022-12",
             "skills": ["AWS", "Helm", "GitOps"]},
        ],
        "education": [{"degree": "B.Sc. Computer Science", "university": "TU Munich", "year": "2021"}],
        "preferences": {"experienceYears": 5,
                        "locations": ["Berlin", "Amsterdam", "London", "Remote"],
                        "keywords": ["site reliability engineer", "sre", "devops engineer",
                                      "platform engineer", "infrastructure engineer"]},
    },
    # ── iOS mobile archetype ──
    {
        "_label": "ios-mobile-3y-usa",
        "id": "test-9",
        "skills": {"technical": ["Swift", "SwiftUI", "UIKit", "Combine",
                                  "Xcode", "Core Data", "iOS", "REST APIs"]},
        "experience": [
            {"title": "iOS Engineer", "company": "Lyft",
             "from": "2023-06", "to": "Present",
             "skills": ["Swift", "SwiftUI", "Combine"]},
            {"title": "Junior iOS Developer", "company": "Robinhood",
             "from": "2022-07", "to": "2023-05",
             "skills": ["Swift", "UIKit"]},
        ],
        "education": [{"degree": "BS Computer Science", "university": "UC San Diego", "year": "2022"}],
        "preferences": {"experienceYears": 3,
                        "locations": ["San Francisco", "Seattle", "New York"],
                        "keywords": ["ios engineer", "ios developer", "mobile engineer"]},
    },
    # ── Android mobile archetype ──
    {
        "_label": "android-mobile-4y-india",
        "id": "test-10",
        "skills": {"technical": ["Kotlin", "Android SDK", "Jetpack Compose",
                                  "Coroutines", "Room", "Retrofit", "Java",
                                  "Dagger Hilt", "RxJava"]},
        "experience": [
            {"title": "Senior Android Engineer", "company": "Swiggy",
             "from": "2023-08", "to": "Present",
             "skills": ["Kotlin", "Jetpack Compose"]},
            {"title": "Android Developer", "company": "Paytm",
             "from": "2021-06", "to": "2023-07",
             "skills": ["Java", "Kotlin", "Retrofit"]},
        ],
        "education": [{"degree": "B.E. CSE", "university": "Delhi Technological University", "year": "2021"}],
        "preferences": {"experienceYears": 4, "locations": ["Bangalore", "Gurgaon"],
                        "keywords": ["android engineer", "android developer", "mobile engineer"]},
    },
    # ── Product Manager archetype (non-engineering, but tech-adjacent) ──
    {
        "_label": "product-manager-5y-usa",
        "id": "test-11",
        "skills": {"technical": ["Product Strategy", "Roadmapping", "A/B Testing",
                                  "SQL", "Product Analytics", "User Research",
                                  "Agile", "JIRA", "Stakeholder Management"]},
        "experience": [
            {"title": "Senior Product Manager", "company": "Stripe",
             "from": "2023-04", "to": "Present",
             "skills": ["Product Strategy", "A/B Testing"]},
            {"title": "Product Manager", "company": "Atlassian",
             "from": "2020-08", "to": "2023-03",
             "skills": ["Roadmapping", "User Research"]},
        ],
        "education": [{"degree": "MBA", "university": "Wharton", "year": "2020"},
                      {"degree": "BS Industrial Engineering", "university": "Georgia Tech", "year": "2017"}],
        "preferences": {"experienceYears": 5,
                        "locations": ["San Francisco", "New York", "Remote"],
                        "keywords": ["product manager", "senior product manager", "product lead"]},
    },
    # ── Engineering Manager archetype (people leadership + tech) ──
    {
        "_label": "engineering-manager-10y-usa",
        "id": "test-12",
        "skills": {"technical": ["People Management", "Java", "Python",
                                  "AWS", "Microservices", "System Design",
                                  "Agile", "Hiring", "Performance Management"]},
        "experience": [
            {"title": "Engineering Manager", "company": "Stripe",
             "from": "2022-05", "to": "Present",
             "skills": ["People Management", "System Design"]},
            {"title": "Tech Lead Manager", "company": "Square",
             "from": "2019-02", "to": "2022-04",
             "skills": ["Java", "Hiring"]},
            {"title": "Senior Software Engineer", "company": "Amazon",
             "from": "2015-07", "to": "2019-01",
             "skills": ["Java", "AWS"]},
        ],
        "education": [{"degree": "MS Computer Science", "university": "CMU", "year": "2015"}],
        "preferences": {"experienceYears": 10,
                        "locations": ["Seattle", "San Francisco", "New York"],
                        "keywords": ["engineering manager", "software engineering manager",
                                      "manager software engineering"]},
    },
    # ── Security engineer archetype (AppSec / red team adjacent) ──
    {
        "_label": "security-engineer-4y-usa",
        "id": "test-13",
        "skills": {"technical": ["Application Security", "Threat Modeling",
                                  "OWASP", "Burp Suite", "Python", "Go",
                                  "Penetration Testing", "Cryptography",
                                  "AWS Security", "SAST", "DAST"]},
        "experience": [
            {"title": "Application Security Engineer", "company": "Cloudflare",
             "from": "2022-09", "to": "Present",
             "skills": ["AppSec", "Threat Modeling", "Python"]},
            {"title": "Security Engineer", "company": "Twilio",
             "from": "2020-06", "to": "2022-08",
             "skills": ["OWASP", "Penetration Testing"]},
        ],
        "education": [{"degree": "BS Computer Science", "university": "Carnegie Mellon", "year": "2020"}],
        "preferences": {"experienceYears": 4,
                        "locations": ["San Francisco", "Austin", "Remote"],
                        "keywords": ["security engineer", "application security engineer",
                                      "appsec engineer", "product security engineer"]},
    },
    # ── QA / SDET archetype (test automation focus) ──
    {
        "_label": "qa-sdet-3y-india",
        "id": "test-14",
        "skills": {"technical": ["Selenium", "Cypress", "Playwright", "Java",
                                  "Python", "TestNG", "JUnit", "REST Assured",
                                  "CI/CD", "Jenkins", "API Testing"]},
        "experience": [
            {"title": "SDET", "company": "Flipkart",
             "from": "2023-05", "to": "Present",
             "skills": ["Playwright", "Java", "TestNG"]},
            {"title": "QA Engineer", "company": "Infosys",
             "from": "2022-01", "to": "2023-04",
             "skills": ["Selenium", "Java"]},
        ],
        "education": [{"degree": "B.Tech IT", "university": "VIT Chennai", "year": "2022"}],
        "preferences": {"experienceYears": 3, "locations": ["Bangalore", "Hyderabad", "Pune"],
                        "keywords": ["sdet", "qa engineer", "test automation engineer",
                                      "software engineer in test"]},
    },
]

# Companies we'll probe (mix of ATS types + finance for MBA profile + infra/data
# for new role-specific profiles). All entries here MUST exist in
# career_scraper._API_SCRAPERS or scrape_company will return [].
COMPANIES = [
    # General tech (existing)
    "comp-amazon", "comp-uber", "comp-stripe", "comp-airbnb",
    # Finance (existing)
    "comp-jpmorgan", "comp-ubs", "comp-goldman", "comp-bofa",
    # New: needed by mobile / data / devops / security archetypes
    "comp-microsoft", "comp-apple", "comp-cloudflare", "comp-databricks",
    "comp-lyft", "comp-asana",
]

# Cap LLM judge cost. Defaults bumped so manual `python -m eval.regression_harness`
# runs cover every profile in PROFILES instead of stopping after the first 6.
MAX_PROFILES = int(os.environ.get("EVAL_MAX_PROFILES", "0")) or len(PROFILES)
MAX_COMPANIES = int(os.environ.get("EVAL_MAX_COMPANIES", "3"))
JUDGE_TOP_N = 10   # judge precision@N


def _ensure_env():
    if not os.environ.get("AZURE_AI_ENDPOINT"):
        print("ERR: set AZURE_AI_ENDPOINT and AZURE_AI_KEY in your shell first")
        sys.exit(2)


def _judge_jobs_with_llm(profile: dict, jobs: list[dict]) -> list[dict]:
    """Ask gpt-4.1 to grade each of the top N jobs as good/maybe/bad for the profile.
    Returns list of {index, verdict, reason} aligned to jobs.
    """
    import openai
    client = openai.AzureOpenAI(
        api_key=os.environ["AZURE_AI_KEY"], api_version="2024-12-01-preview",
        azure_endpoint=os.environ["AZURE_AI_ENDPOINT"], timeout=60.0,
        max_retries=2)

    prefs = profile.get("preferences") or {}
    skills = (profile.get("skills") or {}).get("technical", [])
    exp_lines = "\n".join(
        f"  - {e.get('title','')} @ {e.get('company','')} ({e.get('from','')}→{e.get('to','')})"
        for e in (profile.get("experience") or [])[:5] if isinstance(e, dict))

    profile_block = (
        f"CANDIDATE:\n"
        f"  Years of experience: {prefs.get('experienceYears', 0)}\n"
        f"  Skills: {', '.join(skills[:25])}\n"
        f"  Preferred locations: {', '.join(prefs.get('locations', []))}\n"
        f"  Looking for: {', '.join(prefs.get('keywords', []))}\n"
        f"  Past roles:\n{exp_lines}"
    )
    job_lines = []
    for i, j in enumerate(jobs):
        job_lines.append(
            f"{i+1}. [{j.get('id','')}] {j.get('title','')} | "
            f"Loc: {j.get('location','')} | Skills: {', '.join(j.get('skills', [])[:8])}"
        )

    prompt = (
        f"You are a strict, unbiased recruiter. For EACH job below decide if a "
        f"candidate would realistically apply to and be hired into the role given "
        f"their experience-level, location and skills. Use these verdicts:\n"
        f"  GOOD  – appropriate level, plausible skill alignment, location works.\n"
        f"  MAYBE – stretch role, weak skill overlap, or marginal location.\n"
        f"  BAD   – clearly wrong level, wrong location, or unrelated discipline.\n\n"
        f"{profile_block}\n\n"
        f"JOBS:\n" + "\n".join(job_lines) + "\n\n"
        f"Respond with ONLY a JSON array of objects: "
        f"[{{\"i\": 1, \"v\": \"GOOD|MAYBE|BAD\", \"why\": \"...\"}}]"
    )
    resp = client.chat.completions.create(
        model="gpt41", messages=[{"role": "user", "content": prompt}],
        max_tokens=2000, temperature=0)
    raw = resp.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception as e:
        print("Judge parse fail:", e, raw[:200])
        return []


def evaluate_profile_company(profile: dict, company_id: str) -> dict:
    label = profile["_label"]
    print(f"\n====== {label} x {company_id} ======")
    t0 = time.time()

    # Stage 1: scrape — fan out over ALL keywords AND ALL preferred locations
    # (mirrors the production multi-(query,location) fan-out).
    queries = profile["preferences"]["keywords"] or ["software engineer"]
    locs = [l for l in (profile["preferences"].get("locations") or [])
            if l and l.lower() not in ("remote", "anywhere")][:3] or [""]
    raw_jobs = []
    for q in queries:
        for lq in locs:
            try:
                raw_jobs += scrape_company(company_id, query=q, location=lq)
            except Exception as e:
                print(f"  scrape err q='{q}' loc='{lq}': {e}")
    seen, uniq = set(), []
    for j in raw_jobs:
        if j.get("id") not in seen:
            seen.add(j.get("id")); uniq.append(j)
    raw_jobs = uniq
    n_raw = len(raw_jobs)

    # Stage 2: matcher (filter + score)
    matched = match_jobs_to_profile(raw_jobs, profile)
    n_matched = len(matched)

    # Stage 3: vector rank top 30
    top30 = matched[:30]
    if top30:
        prof_text = profile_to_text(profile)
        prof_emb = generate_embedding(prof_text)
        if prof_emb:
            job_texts = [job_to_text(j) for j in top30]
            job_embs = generate_embeddings_batch(job_texts)
            for j, je in zip(top30, job_embs):
                if je:
                    sim = cosine_similarity(prof_emb, je)
                    j["vectorScore"] = round(sim * 100)
                    j["aiScore"] = round(sim*50 + j.get("matchScore",0)*0.3 + j.get("recencyScore",50)*0.2)
                else:
                    j["vectorScore"] = 0
                    j["aiScore"] = j.get("matchScore", 0)
            top30.sort(key=lambda x: x.get("aiScore", 0), reverse=True)

    # Stage 3b: production LLM rerank on top 20 (also drops bad jobs)
    rerank_slice = top30[:20]
    if len(rerank_slice) >= 3:
        try:
            _ai_rerank_top_jobs(rerank_slice, profile, company_id)
            for j in rerank_slice:
                rs = j.get("aiReasoningScore")
                if rs is not None:
                    j["aiScore"] = round(rs*0.60 + j.get("vectorScore",0)*0.25 + j.get("matchScore",0)*0.15)
            top30 = [j for j in top30 if not j.get("aiDrop") and j.get("aiReasoningScore", 100) >= int(os.environ.get("RERANK_GATE_PRIMARY", "50"))]
            top30.sort(key=lambda x: x.get("aiScore", 0), reverse=True)
        except Exception as e:
            print(f"  rerank err: {e}")

    final = top30[:JUDGE_TOP_N]

    # Stage 4: LLM judge
    judgments = _judge_jobs_with_llm(profile, final) if final else []
    verdict_count = {"GOOD": 0, "MAYBE": 0, "BAD": 0}
    # per-position verdicts so we can compute P@5 alongside P@10
    verdicts_by_pos = ["?" for _ in final]
    bad_titles = []
    for r in judgments:
        v = (r.get("v") or "").upper()
        if v in verdict_count:
            verdict_count[v] += 1
        i = r.get("i", 0) - 1
        if 0 <= i < len(final) and v in verdict_count:
            verdicts_by_pos[i] = v
        if v == "BAD":
            if 0 <= i < len(final):
                bad_titles.append({"title": final[i].get("title",""),
                                   "loc": final[i].get("location",""),
                                   "aiScore": final[i].get("aiScore",0),
                                   "reason": r.get("why","")})

    elapsed = time.time() - t0
    n_top = len(final)
    p_at_n = (verdict_count["GOOD"] / n_top) if n_top else 0.0
    top5 = verdicts_by_pos[:5]
    n5 = len(top5)
    good5 = sum(1 for v in top5 if v == "GOOD")
    bad5 = sum(1 for v in top5 if v == "BAD")
    p_at_5 = (good5 / n5) if n5 else 0.0

    print(f"  raw={n_raw}  matched={n_matched}  judged_top={n_top}  "
          f"GOOD={verdict_count['GOOD']} MAYBE={verdict_count['MAYBE']} BAD={verdict_count['BAD']}  "
          f"P@{JUDGE_TOP_N}={p_at_n:.2f}  P@5={p_at_5:.2f} (G{good5}/B{bad5})  ({elapsed:.1f}s)")
    for b in bad_titles[:3]:
        print(f"    BAD: '{b['title']}' @ {b['loc']} (aiScore={b['aiScore']}) — {b['reason'][:80]}")

    return {
        "profile": label,
        "company": company_id,
        "raw": n_raw,
        "matched": n_matched,
        "good": verdict_count["GOOD"],
        "maybe": verdict_count["MAYBE"],
        "bad": verdict_count["BAD"],
        "p_at_n": p_at_n,
        "p_at_5": p_at_5,
        "good5": good5,
        "bad5": bad5,
        "verdicts_by_pos": verdicts_by_pos,
        "bad_examples": bad_titles[:5],
        "top_titles": [{"t": j.get("title",""), "loc": j.get("location",""),
                        "ai": j.get("aiScore",0), "v": j.get("vectorScore",0),
                        "m": j.get("matchScore",0)} for j in final],
    }


def _companies_for_profile(profile: dict) -> list[str]:
    """Pick MAX_COMPANIES companies that make sense for this profile.

    Routes each archetype to companies that actually staff the role so the
    judge has something real to score. Falls back to general tech for
    anything that doesn't match a specific bucket. Every returned company
    must be in COMPANIES (otherwise scrape_company will be a no-op).
    """
    label = profile.get("_label", "").lower()
    kws = " ".join(profile.get("preferences", {}).get("keywords", [])).lower()

    def _pick(*candidates: str) -> list[str]:
        return [c for c in candidates if c in COMPANIES][:MAX_COMPANIES]

    # Finance / MBA → banks
    if ("mba" in label or "finance" in label
            or any(k in kws for k in ("finance", "investment", "wealth", "banking"))):
        return _pick("comp-jpmorgan", "comp-ubs", "comp-goldman", "comp-bofa")

    # iOS — Apple first, then big consumer apps
    if "ios" in label or "ios engineer" in kws or "ios developer" in kws:
        return _pick("comp-apple", "comp-lyft", "comp-stripe")

    # Android — big consumer / mobile-heavy companies
    if "android" in label or "android engineer" in kws or "android developer" in kws:
        return _pick("comp-uber", "comp-lyft", "comp-amazon")

    # Data engineer (NOT ML — ml has its own profile/company set below)
    if "data-engineer" in label or "data engineer" in kws or "analytics engineer" in kws:
        return _pick("comp-databricks", "comp-stripe", "comp-airbnb")

    # DevOps / SRE / platform
    if any(t in label for t in ("devops", "sre", "platform")) or any(
            k in kws for k in ("site reliability", "devops", "platform engineer", "infrastructure engineer")):
        return _pick("comp-cloudflare", "comp-databricks", "comp-stripe")

    # Security
    if "security" in label or any(k in kws for k in ("security engineer", "appsec", "application security")):
        return _pick("comp-cloudflare", "comp-amazon", "comp-stripe")

    # QA / SDET
    if "qa" in label or "sdet" in label or any(
            k in kws for k in ("sdet", "qa engineer", "test automation", "engineer in test")):
        return _pick("comp-amazon", "comp-microsoft", "comp-uber")

    # Product Manager (non-engineering)
    if "product-manager" in label or "product manager" in kws or "product lead" in kws:
        return _pick("comp-stripe", "comp-airbnb", "comp-asana")

    # Engineering Manager (people-leadership)
    if "engineering-manager" in label or "engineering manager" in kws:
        return _pick("comp-stripe", "comp-amazon", "comp-airbnb")

    # ML / AI engineer (existing senior-7y-usa-ml lands here)
    if "ml" in label.split("-") or any(k in kws for k in ("machine learning", "ml engineer", "ai engineer")):
        return _pick("comp-amazon", "comp-uber", "comp-stripe")

    # Default — general tech
    return _pick("comp-amazon", "comp-uber", "comp-stripe", "comp-airbnb")


def main():
    _ensure_env()
    results = []
    for p in PROFILES[:MAX_PROFILES]:
        for c in _companies_for_profile(p):
            try:
                results.append(evaluate_profile_company(p, c))
            except Exception as e:
                import traceback; traceback.print_exc()
                print(f"  FAIL {p['_label']} x {c}: {e}")

    # Aggregate
    total = len(results)
    avg_p = sum(r["p_at_n"] for r in results) / total if total else 0
    avg_p5 = sum(r.get("p_at_5", 0) for r in results) / total if total else 0
    avg_bad = sum(r["bad"] for r in results) / total if total else 0
    avg_bad5 = sum(r.get("bad5", 0) for r in results) / total if total else 0
    print("\n============ SUMMARY ============")
    print(f"Runs:           {total}")
    print(f"Mean P@{JUDGE_TOP_N}:        {avg_p:.2f}")
    print(f"Mean P@5:        {avg_p5:.2f}")
    print(f"Mean BAD/top10: {avg_bad:.2f}")
    print(f"Mean BAD/top5:  {avg_bad5:.2f}")
    print("\nPer-run:")
    for r in results:
        print(f"  {r['profile']:32s} × {r['company']:14s}  "
              f"raw={r['raw']:3d}  matched={r['matched']:3d}  "
              f"P@10={r['p_at_n']:.2f}  P@5={r.get('p_at_5',0):.2f}  BAD={r['bad']} BAD5={r.get('bad5',0)}")

    out_path = Path(__file__).parent / f"regression_results_{int(time.time())}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
