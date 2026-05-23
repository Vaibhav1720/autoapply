"""Resume microservice — improvement suggestions and aggregate insights.

Endpoints:
  POST /api/v1/resume/suggest-improvements
    Body: { "jobs": [{title, description, company?, aiScore?}], "targetRole"?: str }
    If `jobs` is omitted, falls back to the user's cached `job_results` doc.
    Returns AI-generated, evidence-backed bullet rewrites + missing-keyword
    additions tailored to those job descriptions.

  GET /api/v1/resume/insights?minScore=70&topN=20
    Aggregates the user's previously scored jobs (from job_results) and
    returns: keywords that appear in HIGH-scoring jobs but are missing from
    the resume, weakest sections, and a suggested ATS-friendly headline.
    Pure analysis — no resume rewrite.

Both endpoints are read-mostly: they never mutate the stored profile or
resume. The user can choose to apply suggestions via the existing
/api/v1/profile endpoints.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

import azure.functions as func

from shared.auth_v2 import get_user_id
from shared.blob_client import download_blob
from shared.cosmos_client import get_container, read_item, upsert_item
from shared.embeddings import (
    EMBEDDING_DIMS,
    cosine_similarity,
    generate_embedding,
    generate_embeddings_batch,
    job_to_text,
    profile_to_text,
)
from shared.exceptions import AppException, NotFoundError, RateLimitError, ValidationError
from shared.response_helpers import (
    error_response,
    internal_error_response,
    success_response,
)
from services._runtime import (
    AI_PARSE_MODEL,
    AI_REVIEW_MODEL,
    _check_daily_tailor_quota,
    _is_modern_model,
    get_country_for_billing,
    get_upgrade_message,
    logger,
)

# Resume tailoring — use a fast model for acceptable latency.
# Falls back to the cheaper review model if not configured.
AI_TAILOR_MODEL = os.environ.get("AI_TAILOR_MODEL") or AI_REVIEW_MODEL or AI_PARSE_MODEL

bp = func.Blueprint()


# ── Helpers ────────────────────────────────────────────────────────────────

# Words to ignore when mining "keywords" out of job descriptions. Kept tight
# (~80 entries) — we want skills/tech to surface, not generic English.
_STOPWORDS: set[str] = {
    "and", "the", "for", "with", "you", "your", "our", "are", "have", "has",
    "will", "this", "that", "from", "into", "they", "them", "their", "any",
    "all", "but", "not", "can", "may", "use", "uses", "using", "used", "able",
    "etc", "must", "should", "would", "could", "such", "more", "most", "than",
    "then", "also", "well", "good", "great", "best", "team", "teams", "work",
    "working", "experience", "experiences", "year", "years", "skill", "skills",
    "role", "roles", "ability", "abilities", "candidate", "candidates", "we",
    "us", "be", "is", "of", "in", "on", "at", "to", "or", "as", "by", "an",
    "a", "it", "its", "if", "do", "we'll", "you'll", "what", "when", "who",
    "how", "why", "where", "across", "about", "within", "while", "via", "per",
    "etc.", "high", "low", "new", "old", "very", "much", "many", "some",
    "other", "others", "looking", "join", "build", "help", "make", "want",
    # Generic role / job-title fragments \u2014 these show up in every tech JD
    # and are NOT meaningful "missing keywords" to add to a resume.
    "engineer", "engineers", "engineering", "developer", "developers",
    "development", "software", "system", "systems", "applied", "senior",
    "junior", "staff", "principal", "lead", "manager", "specialist",
    "professional", "associate", "analyst", "consultant", "intern",
    # Generic descriptors \u2014 every tech JD says "scalable", "cloud",
    # "modern", "innovative", etc. They have zero ATS value as keywords.
    "scalable", "scale", "cloud", "modern", "innovative", "fast", "robust",
    "reliable", "performant", "efficient", "flexible", "dynamic", "complex",
    "large", "small", "global", "remote", "hybrid", "onsite", "full",
    "time", "part", "level", "based", "needed", "preferred", "required",
    "responsibilities", "responsibility", "qualifications", "requirements",
    "benefits", "compensation", "salary", "equity", "bonus", "perks",
    "company", "companies", "product", "products", "project", "projects",
    "customer", "customers", "user", "users", "client", "clients",
    "business", "technology", "technologies", "tech", "technical",
    "solution", "solutions", "platform", "service", "services", "tools",
    "tool", "code", "coding", "programming", "languages", "language",
    "design", "designing", "implement", "implementation", "implementing",
    "manage", "managing", "management", "develop", "developing", "developed",
    "include", "includes", "including", "across", "various", "multiple",
    "ensure", "ensuring", "drive", "driving", "support", "supporting",
    "deliver", "delivering", "create", "creating", "creation",
}

# Keep tokens that look like real tech/skill terms: camelCase, dotted (node.js),
# or with a digit (k8s, c++). Anything else passes through length/stopword
# filters below.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\+\#\.\-]{1,30}")


def _tokenize(text: str) -> list[str]:
    """Lowercase, dedupe-friendly token list. Returns ALL occurrences (not
    a set) so callers can compute frequencies."""
    if not text:
        return []
    toks = _TOKEN_RE.findall(text)
    out: list[str] = []
    for t in toks:
        lc = t.lower().strip(".-")
        if len(lc) < 3 or len(lc) > 30:
            continue
        if lc in _STOPWORDS:
            continue
        if lc.isdigit():
            continue
        out.append(lc)
    return out


def _resume_skill_set(profile: dict) -> set[str]:
    """Collect every skill-ish token from the saved profile + parsed resume
    so we can compare against JD vocabulary."""
    bag: list[str] = []
    if not profile:
        return set()
    skills = profile.get("skills") or []
    if isinstance(skills, list):
        for s in skills:
            bag.extend(_tokenize(str(s)))
    # The full profile text covers experience bullets, education, keywords.
    try:
        bag.extend(_tokenize(profile_to_text(profile)))
    except Exception:
        pass
    return set(bag)


# Junk words to never surface as "missing keywords" — generic prose, JD
# boilerplate, soft skills, and noise that always shows up but isn't a
# meaningful resume signal.
_KEYWORD_BLOCKLIST = frozenset({
    "ability", "able", "across", "additional", "advanced", "applicable",
    "applications", "applies", "approach", "approaches", "areas", "around",
    "based", "basic", "best", "better", "build", "building", "business",
    "candidate", "candidates", "change", "client", "clients", "collaborate",
    "collaboration", "collaborative", "communicate", "communication",
    "company", "complete", "complex", "comprehensive", "concepts", "content",
    "contribute", "contribution", "core", "create", "creating", "critical",
    "cross", "culture", "current", "customer", "customers", "data", "day",
    "deep", "deliver", "delivering", "delivery", "demonstrated", "design",
    "designing", "detail", "develop", "developing", "development", "diverse",
    "documentation", "domain", "drive", "driven", "effective", "efficient",
    "effort", "end", "engage", "engineer", "engineering", "ensure", "ensuring",
    "environment", "essential", "established", "etc", "evaluate", "every",
    "excellent", "execute", "execution", "existing", "experience", "expertise",
    "external", "fast", "feedback", "field", "first", "focus", "follow",
    "function", "functional", "future", "general", "global", "goal", "goals",
    "good", "great", "group", "growth", "guide", "handle", "help", "high",
    "highly", "implement", "implementation", "impact", "important", "improve",
    "include", "including", "industry", "information", "initiate", "initiative",
    "innovation", "innovative", "input", "inside", "integrate", "interface",
    "internal", "internally", "key", "knowledge", "large", "lead", "leader",
    "leadership", "leading", "learn", "learning", "level", "leverage",
    "leveraging", "long", "look", "looking", "made", "main", "maintain",
    "make", "making", "manage", "management", "many", "meaningful", "meet",
    "meeting", "method", "methodology", "metric", "metrics", "minimum",
    "modern", "monitor", "monitoring", "multiple", "must", "need", "needs",
    "new", "next", "object", "offer", "office", "open", "operate", "operations",
    "opportunities", "opportunity", "optimize", "order", "organization",
    "organizational", "other", "our", "outcome", "outcomes", "output", "over",
    "overall", "own", "part", "partner", "partners", "passion", "passionate",
    "people", "performance", "person", "place", "plan", "plus", "point",
    "policy", "position", "positive", "possible", "post", "potential",
    "practice", "practices", "preferred", "present", "principle", "principles",
    "prior", "priorities", "prioritize", "priority", "problem", "problems",
    "process", "processes", "produce", "product", "production", "productivity",
    "professional", "program", "project", "projects", "proven", "provide",
    "provides", "providing", "quality", "quickly", "range", "real", "reach",
    "ready", "reason", "receive", "recommend", "record", "regular", "related",
    "relationship", "relevant", "report", "reporting", "request", "requirement",
    "requirements", "research", "resource", "resources", "responsibilities",
    "responsibility", "responsible", "result", "results", "review", "right",
    "risk", "road", "roadmap", "role", "roles", "scale", "scaling", "scope",
    "search", "second", "see", "seek", "seeking", "self", "sell", "senior",
    "service", "services", "set", "share", "ship", "shipping", "short", "show",
    "side", "significant", "similar", "site", "skill", "skills", "small",
    "social", "solid", "solution", "solutions", "solve", "solving", "source",
    "specific", "speed", "stack", "stage", "staff", "stakeholder", "stakeholders",
    "standard", "standards", "start", "state", "step", "stop", "story", "strategic",
    "strategy", "strong", "structure", "study", "subject", "success", "successful",
    "suggest", "suitable", "support", "system", "systems", "table", "talent",
    "target", "task", "tasks", "team", "teams", "tech", "technical", "technology",
    "test", "testing", "the", "them", "they", "thinking", "this", "those",
    "three", "through", "throughout", "tier", "time", "tool", "tools", "top",
    "total", "track", "transform", "transparent", "trend", "trends", "true",
    "trust", "type", "ultimate", "understand", "understanding", "unique",
    "update", "upon", "use", "user", "users", "using", "value", "values",
    "various", "vendor", "verify", "version", "view", "vision", "visit",
    "want", "way", "ways", "well", "what", "when", "where", "who", "whole",
    "why", "wide", "will", "win", "wins", "within", "without", "work", "working",
    "workplace", "world", "would", "write", "writing", "year", "years", "your",
    # common misspellings of generic terms
    "productivety", "productivty", "managment", "developement", "experiance",
    "responsibilty", "communcation", "infomation",
    # soft skills that aren't resume "missing keywords"
    "agile", "ownership", "accountability", "creativity", "curiosity",
    "empathy", "humility", "integrity", "mentor", "mentoring",
    # generic 3-4 letter tech abbreviations + brand/role nouns that
    # routinely leak in as "missing" but are noise to a candidate
    "risk", "dev", "devs", "prod", "qa", "sre", "ops", "hr", "pm", "po",
    "alexa", "siri", "cortana", "echo", "nest", "prime", "aws", "gcp",
    "promotions", "promotion", "campaigns", "campaign", "launches", "launch",
    "hires", "hiring", "interview", "interviews", "onboarding", "resume",
    "resumes", "benefits", "perks", "compensation", "salary", "bonus",
    "equity", "stock", "options", "holiday", "holidays", "leave", "vacation",
    "office", "remote", "hybrid", "onsite", "location", "locations", "shift",
    "weekend", "evening", "morning", "day", "days", "hour", "hours",
    "minute", "minutes", "month", "months", "week", "weeks",
})


def _looks_like_misspelling(word: str) -> bool:
    """Heuristic: reject words that look like JD typos (e.g. 'productivety').
    Real technical terms either have proper casing in JDs (Kubernetes, Terraform),
    a digit/symbol (k8s, c++), or a dot (node.js)."""
    if any(c in word for c in ".-+#"):
        return False  # node.js, c++, c#, k8s-style — keep
    # If a vowel-consonant pattern looks off (e.g. ends in -ety/-ety where -ity is correct)
    if word.endswith("ety") and not word.endswith("ciety") and not word.endswith("riety"):
        return True
    if word.endswith("tion") and len(word) < 7:
        return True
    return False


def _filter_quality_keywords(keywords: list[str], jd_blob: str) -> list[str]:
    """Drop generic prose, soft skills, misspellings, and short tokens so
    only credible technical/skill terms remain. Prefer terms that appear
    proper-cased in the JD (proxy for proper nouns / named tools)."""
    if not keywords:
        return []
    jd_words = set(re.findall(r"\b[A-Za-z][A-Za-z0-9\+\#\.\-]{1,30}\b", jd_blob))
    proper_cased_lc = {
        w.lower() for w in jd_words
        if (w[0].isupper() and not w.isupper()) or any(c in w for c in ".+#-")
        or any(c.isupper() for c in w[1:])  # camelCase
    }
    out: list[str] = []
    for kw in keywords:
        # Reject anything shorter than 5 chars unless it has a digit/symbol
        # (k8s, c++, c#, .net are legit short tokens). Single short words
        # are almost always junk: dev, qa, ops, risk, etc.
        if len(kw) < 5 and not any(c.isdigit() or c in ".+#" for c in kw):
            continue
        if kw in _KEYWORD_BLOCKLIST:
            continue
        if _looks_like_misspelling(kw):
            continue
        # Keep tokens that appeared proper-cased in at least one JD
        # (strong signal it's a named tool/framework rather than prose).
        if kw not in proper_cased_lc:
            continue
        out.append(kw)
    return out[:15]


def _collect_user_jobs(user_id: str) -> list[dict]:
    """Pull jobs from the user's cached results document, flattened.
    Tolerates the two shapes we use: top-level `jobs` and `grouped[].jobs`."""
    doc = read_item("job_results", f"results-{user_id}", user_id) or {}
    out: list[dict] = []
    flat = doc.get("jobs") or []
    if isinstance(flat, list):
        for j in flat:
            if isinstance(j, dict):
                out.append(j)
    grouped = doc.get("grouped") or []
    if isinstance(grouped, list):
        for g in grouped:
            if not isinstance(g, dict):
                continue
            for j in g.get("jobs") or []:
                if isinstance(j, dict):
                    out.append(j)
    return out


def _job_score(j: dict) -> int:
    raw = j.get("aiScore", j.get("matchScore", 0))
    try:
        return int(raw)
    except Exception:
        return 0


def _job_text(j: dict) -> str:
    """Best-effort flatten of a job document to a single text blob."""
    parts: list[str] = []
    for k in ("title", "description", "snippet", "summary", "requirements"):
        v = j.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.extend(str(x) for x in v if x)
    return "\n".join(parts)


def _ai_client():
    """Return (client, ok). ok=False if env not configured."""
    endpoint = os.environ.get("AZURE_AI_ENDPOINT", os.environ.get("OPENAI_ENDPOINT", ""))
    key = os.environ.get("AZURE_AI_KEY", os.environ.get("OPENAI_KEY", ""))
    if not (endpoint and key):
        return None, False
    try:
        import openai

        return openai.AzureOpenAI(
            api_key=key,
            api_version="2024-12-01-preview",
            azure_endpoint=endpoint,
        ), True
    except Exception as e:
        logger.warning("[RESUME] AI client init failed: %s", e)
        return None, False


def _load_full_resume_text(profile: dict) -> str:
    """Return the candidate's actual resume body text (the raw extracted PDF
    text, including bullets). Cached on the profile after first call so the
    paid tailoring endpoint never re-downloads/re-parses the PDF.

    Falls back to ``profile_to_text(profile)`` if the PDF can't be fetched
    \u2014 the model still gets *something* to ground against.
    """
    docs = profile.get("documents") or {}
    parsed = docs.get("parsedResumeData") or {}
    cached = parsed.get("fullText")
    if isinstance(cached, str) and len(cached) > 200:
        return cached

    resume_url = docs.get("resumeUrl") or ""
    version = docs.get("resumeVersion") or 0
    user_id = profile.get("id") or profile.get("userId") or ""
    if not (resume_url and user_id and version):
        return profile_to_text(profile)

    blob_name = f"{user_id}/resume_v{version}.pdf"
    try:
        data = download_blob("resumes", blob_name)
        import io
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [p.extract_text() or "" for p in reader.pages]
        text = "\n".join(pages).strip()
        if len(text) < 200:
            return profile_to_text(profile)
        # Cache on profile for next time \u2014 cheap one-time write.
        try:
            parsed["fullText"] = text[:30000]
            docs["parsedResumeData"] = parsed
            profile["documents"] = docs
            upsert_item("profiles", profile)
        except Exception as e:
            logger.warning("[RESUME_TAILOR] cache fullText failed: %s", e)
        return text
    except Exception as e:
        logger.warning("[RESUME_TAILOR] PDF fetch/parse failed (%s) \u2014 falling back", e)
        return profile_to_text(profile)


def _select_relevant_jobs(profile: dict, jobs: list[dict], k: int) -> list[dict]:
    """Pick the K jobs most semantically aligned with the candidate's profile.

    Top-scored jobs are not always the best targets to optimize a resume
    against \u2014 they may include outliers. Embedding similarity gives us
    jobs the candidate *actually fits*, which is what tailoring should
    optimize for.
    """
    if len(jobs) <= k:
        return jobs

    profile_emb = profile.get("profileEmbedding") or []
    if not profile_emb or len(profile_emb) != EMBEDDING_DIMS:
        try:
            profile_emb = generate_embedding(profile_to_text(profile))
        except Exception:
            profile_emb = []

    if not profile_emb:
        # Embeddings unavailable \u2014 fall back to score sort.
        return sorted(jobs, key=_job_score, reverse=True)[:k]

    try:
        texts = [job_to_text(j) for j in jobs]
        embs = generate_embeddings_batch(texts)
    except Exception as e:
        logger.warning("[RESUME_TAILOR] job embeddings failed (%s) \u2014 score-sort fallback", e)
        return sorted(jobs, key=_job_score, reverse=True)[:k]

    scored: list[tuple[float, dict]] = []
    for j, emb in zip(jobs, embs):
        if not emb:
            sim = 0.0
        else:
            sim = cosine_similarity(profile_emb, emb)
        # Hybrid: 70% semantic similarity, 30% original aiScore (already
        # normalized to 0\u2013100).
        hybrid = sim * 0.70 + (_job_score(j) / 100.0) * 0.30
        scored.append((hybrid, j))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [j for _, j in scored[:k]]


def _select_relevant_jobs_vector(profile: dict, user_id: str, k: int) -> list[dict]:
    """Pull the K best-fit jobs straight from Cosmos via VectorDistance().

    Replaces _select_relevant_jobs when persisted vectors are available:
    no Python re-embedding, no in-memory cosine loop \u2014 a single Cosmos
    query against the user's partition. Falls back to None if the profile
    embedding is missing or the vector container has no jobs yet.
    """
    profile_emb = profile.get("profileEmbedding") or []
    if not profile_emb or len(profile_emb) != EMBEDDING_DIMS:
        try:
            profile_emb = generate_embedding(profile_to_text(profile))
        except Exception:
            return None  # type: ignore[return-value]
    if not profile_emb:
        return None  # type: ignore[return-value]

    try:
        container = get_container("job_vectors")
        query = (
            "SELECT TOP @topK c.id, c.jobId, c.companyId, c.company, c.title, "
            "c.url, c.location, c.snippet, c.summary, c.description, "
            "c.aiScore, c.vectorScore, c.matchScore, c.skills, "
            "VectorDistance(c.embedding, @vec) AS similarityScore "
            "FROM c WHERE c.userId = @uid "
            "ORDER BY VectorDistance(c.embedding, @vec)"
        )
        rows = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@topK", "value": max(k, 10)},
                {"name": "@vec", "value": profile_emb},
                {"name": "@uid", "value": user_id},
            ],
            partition_key=user_id,
        ))
    except Exception as e:
        logger.warning("[RESUME_TAILOR] vector_search failed (%s) \u2014 fallback path", e)
        return None  # type: ignore[return-value]

    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        r.pop("similarityScore", None)
        out.append(r)
    return out[:k] if out else None  # type: ignore[return-value]


def _peer_signal_keywords(profile: dict, user_id: str, exclude_resume_text: str,
                          k: int = 30) -> list[dict]:
    """Mine high-aiScore jobs from PEER profiles (other users) whose target
    market overlaps with this candidate's, and return the keywords that
    differentiate winners from the average pool.

    This is the "compare to other resumes" signal: by looking at jobs that
    OTHER strong candidates with a semantically similar profile have ranked
    well against, we surface vocabulary that this candidate is plausibly
    missing relative to actual peer competition \u2014 not just relative to
    a stack of JDs.

    Always returns a list (possibly empty). Best-effort \u2014 swallows all
    Cosmos / embedding failures so the main tailoring path never breaks.
    """
    profile_emb = profile.get("profileEmbedding") or []
    if not profile_emb or len(profile_emb) != EMBEDDING_DIMS:
        try:
            profile_emb = generate_embedding(profile_to_text(profile))
        except Exception:
            return []
    if not profile_emb:
        return []

    try:
        container = get_container("job_vectors")
        # Cross-partition: scan all peers, pick the top ~120 jobs across the
        # whole vector store closest to this candidate, then filter to ones
        # that scored well for THEIR owner. The peer's userId is excluded.
        query = (
            "SELECT TOP @topK c.userId, c.title, c.skills, c.aiScore, "
            "c.snippet, c.summary, c.description, "
            "VectorDistance(c.embedding, @vec) AS similarityScore "
            "FROM c WHERE c.userId != @uid "
            "ORDER BY VectorDistance(c.embedding, @vec)"
        )
        rows = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@topK", "value": 120},
                {"name": "@vec", "value": profile_emb},
                {"name": "@uid", "value": user_id},
            ],
            enable_cross_partition_query=True,
        ))
    except Exception as e:
        logger.warning("[RESUME_TAILOR] peer_signal query failed: %s", e)
        return []

    if not rows:
        return []

    # Keep only rows where the peer ranked the job high \u2014 those are the
    # success signals. Anything below 65 is noise / poor match for them too.
    winners = [r for r in rows if isinstance(r, dict) and (r.get("aiScore") or 0) >= 65]
    if not winners:
        return []

    resume_text_lc = (exclude_resume_text or "").lower()
    resume_skills = _resume_skill_set(profile)
    freq: Counter[str] = Counter()
    job_count: Counter[str] = Counter()
    for w in winners:
        text_parts = [
            w.get("title", ""),
            w.get("snippet", "") or w.get("summary", "") or w.get("description", ""),
        ]
        if isinstance(w.get("skills"), list):
            text_parts.extend(str(s) for s in w["skills"])
        toks = _tokenize(" ".join(text_parts))
        for t in set(toks):
            job_count[t] += 1
        for t in toks:
            freq[t] += 1

    out: list[dict] = []
    for tok, n in job_count.most_common(80):
        if n < 3:  # appears in <3 peer winners \u2014 too noisy
            continue
        if tok in resume_skills:
            continue
        if tok in resume_text_lc:
            continue
        out.append({
            "keyword": tok,
            "peerWinnerCount": n,
            "totalMentions": freq[tok],
        })
        if len(out) >= k:
            break
    if out:
        logger.info("[RESUME_TAILOR] peer_signal: %d candidate keywords from %d peer winners",
                    len(out), len(winners))
    return out


def _critique_and_refine(client, draft_md: str, profile_json: str,
                         resume_text: str, jd_blob: str, target_focus: str,
                         industry: str, model_name: str) -> str:
    """Second-pass quality gate: ask the model to grade its own draft against
    the rubric (specificity, no fabrication, evidence-cited) and rewrite
    weak sections in place. Returns the refined Markdown, or the original
    draft if anything goes wrong.

    This is the iteration step that pushes raw single-shot output toward
    the "Opus 4.7"-class quality the candidate is paying for.
    """
    if not draft_md.strip():
        return draft_md
    try:
        critique_prompt = (
            "You are reviewing a previous draft of resume-tailoring advice. "
            "Your job is to RAISE QUALITY, not to start over. Read the draft, "
            "audit each suggestion against the rubric, then output a CLEANED, "
            "STRONGER version of the same Markdown.\n\n"
            f"Industry: {industry.upper().replace('_', '/')}.\n"
            f"Target roles the candidate is searching: {target_focus}.\n\n"
            "Rubric (apply silently \u2014 do NOT print the audit):\n"
            "  1. Every \"missing keyword\" must (a) be a concrete tool/"
            "framework/methodology, NOT a generic word, (b) be substring-"
            "absent from the resume body below, (c) appear in 2+ JDs, and "
            "(d) be plausibly supported by the candidate's evidence. "
            "DROP any keyword that fails any of these.\n"
            "  2. Every bullet rewrite's `**Original**` quote must be a "
            "verbatim line from the resume body. If the draft fabricated "
            "an original, REPLACE it with a real bullet from the resume "
            "body or remove the rewrite entirely.\n"
            "  3. Every rewrite must be one line, action-verb-led, and "
            "include scope + a quantified outcome where the resume "
            "supplies the number (do NOT invent numbers).\n"
            "  4. The headline / summary must be \u226422 words, "
            "industry-appropriate, and reflect the candidate's actual "
            "experience \u2014 not aspirational titles.\n"
            "  5. Skills section additions must each cite specific evidence "
            "from the resume.\n"
            "  6. Quick wins must be surgical (each <10 minutes) and "
            "specific (\"merge bullets 2 and 3 in role X\", not \"improve "
            "formatting\").\n"
            "  7. NO fluff. NO \"consider doing X\". NO praise. Cut "
            "anything that isn't directly actionable.\n\n"
            "Output the cleaned Markdown ONLY \u2014 same section headers, "
            "same order. If a section now has fewer items because some "
            "failed the rubric, that is correct. Quality over quantity.\n\n"
            "=== STRUCTURED PROFILE ===\n"
            f"{profile_json}\n\n"
            "=== RESUME BODY ===\n"
            f"{resume_text[:12000]}\n\n"
            "=== TARGET JOBS (truncated) ===\n"
            f"{jd_blob[:8000]}\n\n"
            "=== DRAFT TO REFINE ===\n"
            f"{draft_md}"
        )
        kwargs: dict = {
            "model": model_name,
            "messages": [{"role": "user", "content": critique_prompt}],
        }
        if _is_modern_model(model_name):
            kwargs["max_completion_tokens"] = 12000
            if model_name.lower().replace("-", "").startswith("gpt5"):
                # Refinement benefits from more thinking than the first
                # pass \u2014 the model has the draft + rubric already.
                kwargs["reasoning_effort"] = "medium"
        else:
            kwargs["max_tokens"] = 4000
            kwargs["temperature"] = 0.15
        resp = client.chat.completions.create(**kwargs)
        refined = (resp.choices[0].message.content or "").strip()
        if len(refined) < 200:
            # Refinement collapsed \u2014 keep the original draft.
            logger.warning("[RESUME_TAILOR] refine pass too short (%d chars), keeping draft",
                           len(refined))
            return draft_md
        logger.info("[RESUME_TAILOR] refine pass: %d -> %d chars",
                    len(draft_md), len(refined))
        return refined
    except Exception as e:
        logger.warning("[RESUME_TAILOR] refine pass failed: %s", e)
        return draft_md


# ── POST /api/v1/resume/suggest-improvements ──────────────────────────────


@bp.route(route="api/v1/resume/suggest-improvements", methods=["POST"])
def suggest_resume_improvements(req: func.HttpRequest) -> func.HttpResponse:
    """Tailored resume rewrite suggestions against a target set of jobs."""
    try:
        user_id = get_user_id(req)
        body = req.get_json() if req.get_body() else {}
        target_role = (body.get("targetRole") or "").strip()
        # Optional list of titles the user is currently searching for —
        # surfaced from the Discover screen chips. Combined with target_role
        # so the prompt knows EXACTLY which roles to optimise for.
        body_target_titles = body.get("targetTitles") or body.get("titles") or []
        if not isinstance(body_target_titles, list):
            body_target_titles = []
        body_target_titles = [str(t).strip() for t in body_target_titles if str(t).strip()][:6]
        # Industry the user is currently searching in (same vocab as the
        # Discover screen pill). Drives industry-aware prompting and is
        # persisted to the profile so the rest of the system stays in sync.
        body_industry = (body.get("industry") or "").strip().lower() or None
        provided_jobs = body.get("jobs")
        # Pull a focused candidate pool so the model sees enough JD diversity
        # to spot real cross-job patterns, without blowing latency. The
        # vector-DB path can handle a much larger pool than the in-memory
        # selector — we keep per-JD blob trimmed inside the prompt builder
        # so prompt cost stays bounded.
        max_jobs = int(os.environ.get("RESUME_TAILOR_MAX_JOBS", "50"))

        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        allowed, _remaining = _check_daily_tailor_quota(profile)
        if not allowed:
            country = get_country_for_billing(req, profile)
            raise RateLimitError(
                get_upgrade_message(country)
                + " Resume tailoring is limited to 1 run per day on the free plan."
            )

        # Persist any new industry choice BEFORE we read prefs below, so the
        # rest of this request (and the next Discover call) sees it.
        if body_industry:
            prefs_p = profile.setdefault("preferences", {})
            if prefs_p.get("industry") != body_industry:
                prefs_p["industry"] = body_industry
                try:
                    upsert_item("profiles", profile)
                except Exception as e:
                    logger.warning("[RESUME_TAILOR] persist industry failed: %s", e)

        # Source the jobs to optimize against. Prefer caller-supplied list
        # (lets the UI pick "this set" intentionally), else use embedding
        # similarity to pick the candidate's BEST-FIT jobs from cached
        # results \u2014 those are the realistic targets to optimize for.
        jobs: list[dict] = []
        if isinstance(provided_jobs, list) and provided_jobs:
            for j in provided_jobs:
                if isinstance(j, dict):
                    jobs.append(j)
            # Even when the caller supplies jobs, run embedding-based
            # selection if the pool is bigger than max_jobs so we tailor
            # against the candidate's BEST-FIT subset.
            if len(jobs) > max_jobs:
                jobs = _select_relevant_jobs(profile, jobs, max_jobs)
        else:
            # Prefer Cosmos VectorDistance() over the persisted job_vectors
            # container — single query, no Python re-embedding.
            vec_jobs = _select_relevant_jobs_vector(profile, user_id, max_jobs)
            if vec_jobs:
                logger.info("[RESUME_TAILOR] vector path: %d jobs from job_vectors",
                            len(vec_jobs))
                jobs = vec_jobs
            else:
                cached = _collect_user_jobs(user_id)
                jobs = _select_relevant_jobs(profile, cached, max_jobs)

        if not jobs:
            return success_response({
                "targetRole": target_role,
                "industry": "",
                "targetTitles": [],
                "missingKeywords": [],
                "suggestionsMarkdown": "",
                "aiAvailable": False,
                "model": "",
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "noJobsMessage": (
                    "No discover results yet. Head to the Discover tab, run a job "
                    "search for your target roles, and then come back here to tailor "
                    "your resume against those results."
                ),
            })

        # Build the "target roles" block that drives the prompt. Priority:
        #   1. body.targetTitles (chips the user is actively searching with)
        #   2. body.targetRole (legacy single-role field)
        #   3. profile.preferences.keywords (last saved Discover query)
        prefs = profile.get("preferences") or {}
        saved_keywords = [
            str(k).strip() for k in (prefs.get("keywords") or []) if str(k).strip()
        ][:6]
        target_titles_combined: list[str] = []
        seen_t = set()
        for src in (body_target_titles, [target_role] if target_role else [], saved_keywords):
            for t in src:
                key = t.lower()
                if key and key not in seen_t:
                    seen_t.add(key)
                    target_titles_combined.append(t)
        target_focus = ", ".join(target_titles_combined) or "their current focus"

        # Pull the actual resume body text (real bullets, real wording) so the
        # model can quote the candidate's own language rather than guessing.
        resume_text = _load_full_resume_text(profile)
        resume_text_lc = resume_text.lower()

        # Pre-compute keyword gap so we can hand the model a curated list.
        resume_skills = _resume_skill_set(profile)
        jd_blob = "\n".join(_job_text(j) for j in jobs)
        jd_tokens = _tokenize(jd_blob)
        jd_freq = Counter(jd_tokens)
        per_job_seen: Counter[str] = Counter()
        for j in jobs:
            for t in set(_tokenize(_job_text(j))):
                per_job_seen[t] += 1
        common = [t for t, n in per_job_seen.items() if n >= 2]
        common.sort(key=lambda t: jd_freq[t], reverse=True)
        # Belt-and-braces: drop tokens that appear anywhere in the raw resume
        # text too (the tokenizer-only check above misses substrings inside
        # multi-word skills like "Apache Kafka").
        missing_keywords = [
            t for t in common
            if t not in resume_skills and t not in resume_text_lc
        ][:25]

        # Filter out junk: generic words, misspellings, very short tokens.
        # A keyword should be a proper-cased multi-word term or a known
        # technical term — not random JD prose like "productivety".
        missing_keywords = _filter_quality_keywords(missing_keywords, jd_blob)

        # Cross-resume signal: keywords from JOBS that PEERS with similar
        # profiles ranked highly. Boosts the missing-keyword list with terms
        # the candidate's actual competition is showcasing.
        peer_signal = _peer_signal_keywords(profile, user_id, resume_text)
        peer_keywords = [p["keyword"] for p in peer_signal][:15]
        # Merge peer terms in (they're already de-duplicated against the
        # candidate's resume) but keep them after the JD-grounded ones so the
        # model still prioritises terms backed by the actual target JDs.
        for t in peer_keywords:
            if t not in missing_keywords:
                missing_keywords.append(t)
        missing_keywords = missing_keywords[:30]

        # Compact structured profile for the model \u2014 explicit sections
        # are easier to reason over than the flat text dump.
        structured_profile = {
            "headline": (profile.get("headline") or "").strip(),
            "summary": (
                profile.get("aiSummary")
                or ((profile.get("documents") or {}).get("parsedResumeData") or {}).get("extractedSummary", "")
                or ""
            ),
            "skills": ((profile.get("skills") or {}).get("technical") or [])[:80],
            "experience": [
                {
                    "title": (e or {}).get("title", ""),
                    "company": (e or {}).get("company", ""),
                    "from": (e or {}).get("from", ""),
                    "to": (e or {}).get("to", ""),
                }
                for e in (profile.get("experience") or [])[:8]
            ],
            "education": [
                {
                    "degree": (e or {}).get("degree", ""),
                    "university": (e or {}).get("university", ""),
                    "year": (e or {}).get("year", ""),
                }
                for e in (profile.get("education") or [])[:5]
            ],
            "yearsExperience": (
                ((profile.get("documents") or {}).get("parsedResumeData") or {}).get("totalYearsExperience", 0)
                or (profile.get("preferences") or {}).get("experienceYears", 0)
            ),
        }

        client, ok = _ai_client()
        suggestions_md = ""
        model_used = ""
        if ok:
            try:
                # Send richer JD context \u2014 1500 chars per JD vs 600 before.
                jd_summaries = []
                for i, j in enumerate(jobs, 1):
                    blob = _job_text(j)
                    jd_summaries.append(
                        f"[{i}] {j.get('title', '')} @ {j.get('company', '')} "
                        f"(score={_job_score(j)})\n{blob[:800]}"
                    )
                # Cap resume text at 6K chars — enough for any normal resume
                # while keeping prompt cost and latency sane.
                resume_for_prompt = resume_text[:6000]
                profile_json = json.dumps(structured_profile, ensure_ascii=False)

                prompt = (
                    "You are a senior tech recruiter and resume coach writing "
                    "directly to the candidate. The candidate is in the "
                    f"'{((profile.get('preferences') or {}).get('industry') or 'tech').upper().replace('_','/')}' industry, "
                    f"actively searching for: {target_focus}. "
                    "Your output is the polished, customer-facing advice they "
                    "see — write naturally, like a professional coach speaking "
                    "to them. Do NOT mention internal limits, counts, or "
                    "instructions (e.g. never write 'up to 3 bullets', 'top 5 "
                    "keywords', or numbered section labels). Do NOT include "
                    "meta-commentary about what you can or can't do.\n\n"
                    "HARD CONSTRAINTS — never violate:\n"
                    "1. Do NOT invent skills, tools, projects, employers, "
                    "dates, or accomplishments. Every suggestion must be "
                    "grounded in something already present in the resume.\n"
                    "2. Do NOT suggest changing past job titles, company "
                    "names, dates, or degrees. They are immutable facts.\n"
                    "3. Do NOT recommend a career pivot or different "
                    "specialty. Optimize what the candidate IS.\n"
                    "4. Every keyword you recommend MUST be: (a) a concrete "
                    "named tool/framework/language/platform (e.g. Kubernetes, "
                    "Terraform, PyTorch, gRPC, Snowflake) — NOT a generic "
                    "word, soft skill, or industry buzzword, (b) appear in "
                    "multiple target JDs below, (c) genuinely absent from "
                    "the resume body, and (d) plausibly something the "
                    "candidate could honestly add based on existing evidence. "
                    "If a candidate keyword looks like a typo, misspelling, "
                    "generic noun, or soft skill — SILENTLY DROP IT. Never "
                    "surface low-quality terms to the user.\n"
                    "5. Bullet rewrites must quote the candidate's "
                    "ORIGINAL bullet verbatim from the resume body.\n\n"
                    "Write the response as polished Markdown with these "
                    "sections (use these exact, natural headings — no "
                    "numbering, no counts in headings):\n\n"
                    "## Keywords to weave in\n"
                    "A short bulleted list of concrete tools/technologies "
                    "worth adding to the resume. Each item:\n"
                    "`**Keyword** — why it matters for these roles, and "
                    "where in your background it fits naturally.`\n"
                    "Only include items that pass every constraint above. "
                    "It is perfectly fine to list very few — quality over "
                    "quantity. If none qualify, write a one-line note that "
                    "the resume already covers the in-demand vocabulary.\n\n"
                    "## Bullets to strengthen\n"
                    "Pick a few of the weakest existing bullets and rewrite "
                    "them. Same role, same company, only the bullet text "
                    "changes. Format each as:\n"
                    "  - **Role:** <exact title> @ <exact company>\n"
                    "  - **Current:** \"<exact verbatim quote from the "
                    "resume body>\"\n"
                    "  - **Suggested:** <one line: action verb + scope + "
                    "quantified outcome, weaving in relevant keywords only "
                    "if the candidate's evidence supports them>\n"
                    "  - **Why this lands better:** <one short sentence>\n\n"
                    "## Headline\n"
                    "One sharp sentence (≤22 words) tailored to the common "
                    "theme across these target roles. Reflect what the "
                    "candidate actually does today.\n\n"
                    "## Skills to add\n"
                    "A short bulleted list of concrete skills/tools to add "
                    "to the skills section. Each MUST cite evidence:\n"
                    "`**Skill** — evidenced by: <which bullet/role/project "
                    "on the resume shows the candidate has used it>`. "
                    "No evidence → omit the skill entirely.\n\n"
                    "## Quick wins\n"
                    "A few short, surgical edits the candidate can make in "
                    "under ten minutes (formatting, ordering, removing "
                    "filler, fixing inconsistent tense). Bulleted list.\n\n"
                    "Tone: professional, encouraging, specific. No fluff, "
                    "no 'consider doing X' hedging, no meta talk about the "
                    "format. Give exact text the candidate can paste in.\n\n"
                    "=== CANDIDATE KEYWORD POOL (pre-filtered — reject any "
                    "that look like prose, typos, or soft skills) ===\n"
                    f"{', '.join(missing_keywords) or '(none — say so politely)'}\n\n"
                    "=== STRUCTURED PROFILE (skills/experience/education) "
                    "===\n"
                    f"{profile_json}\n\n"
                    "=== RESUME BODY (raw text — use this to quote "
                    "real bullets and verify substring presence) ===\n"
                    f"{resume_for_prompt}\n\n"
                    f"=== TARGET JOBS ({len(jobs)}) ===\n"
                    + "\n\n".join(jd_summaries)
                )

                kwargs: dict = {
                    "model": AI_TAILOR_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if _is_modern_model(AI_TAILOR_MODEL):
                    # GPT-5 / o-series spend invisible reasoning tokens out
                    # of this same budget. Give them plenty of room or the
                    # response comes back empty. We also pin reasoning to a
                    # low effort — this is structured rewriting, not math
                    # olympiad, so deep reasoning is wasted spend + latency.
                    kwargs["max_completion_tokens"] = 12000
                    if AI_TAILOR_MODEL.lower().replace("-", "").startswith("gpt5"):
                        kwargs["reasoning_effort"] = "low"
                else:
                    kwargs["max_tokens"] = 3500
                    kwargs["temperature"] = 0.2
                resp = client.chat.completions.create(**kwargs)
                suggestions_md = (resp.choices[0].message.content or "").strip()
                model_used = AI_TAILOR_MODEL
                logger.info(
                    "[RESUME_SUGGEST] model=%s jobs=%d resume_chars=%d out_chars=%d",
                    AI_TAILOR_MODEL, len(jobs), len(resume_for_prompt),
                    len(suggestions_md),
                )

                # Iteration: a critique + refine pass that audits the draft
                # against the rubric and rewrites weak sections in place.
                # Off by default (latency), turn on with RESUME_TAILOR_REFINE=1.
                # Skipped automatically if the draft is empty.
                want_refine = os.environ.get("RESUME_TAILOR_REFINE", "0") == "1"
                if want_refine and suggestions_md:
                    suggestions_md = _critique_and_refine(
                        client, suggestions_md, profile_json,
                        resume_for_prompt, jd_blob,
                        target_focus,
                        ((profile.get('preferences') or {}).get('industry') or 'tech'),
                        AI_TAILOR_MODEL,
                    )
            except Exception as e:
                logger.warning("[RESUME_SUGGEST] AI call failed: %s", e)
                suggestions_md = ""

        # We deliberately do NOT return the raw `missing_keywords` list to
        # the client any more. Even with aggressive filtering, single-token
        # chips like "risk", "dev", "alexa", "promotions" leak through and
        # erode user trust. The LLM's "Keywords to weave in" section in the
        # markdown is far more reliable because it requires evidence and
        # natural-language framing. Better to show nothing than to show
        # something misleading.
        return success_response({
            "targetRole": target_role,
            "industry": ((profile.get('preferences') or {}).get('industry') or ''),
            "targetTitles": target_titles_combined,
            "missingKeywords": [],
            "suggestionsMarkdown": suggestions_md,
            "aiAvailable": ok and bool(suggestions_md),
            "model": model_used,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Resume suggest-improvements error")
        return internal_error_response(str(e))


# ── GET /api/v1/resume/insights ────────────────────────────────────────────


@bp.route(route="api/v1/resume/insights", methods=["GET"])
def resume_insights(req: func.HttpRequest) -> func.HttpResponse:
    """Aggregate analysis: what differentiates the user's BEST-matched jobs
    from the rest, and how the resume falls short of those."""
    try:
        user_id = get_user_id(req)
        try:
            min_score = int(req.params.get("minScore", "70"))
        except ValueError:
            min_score = 70
        try:
            top_n = int(req.params.get("topN", "20"))
        except ValueError:
            top_n = 20
        top_n = max(5, min(top_n, 100))

        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        all_jobs = _collect_user_jobs(user_id)
        if not all_jobs:
            return success_response({
                "totalJobsAnalysed": 0,
                "highScoreCount": 0,
                "topMissingKeywords": [],
                "topMatchedKeywords": [],
                "suggestedHeadline": "",
                "message": "No discover results yet — run a search first.",
            })

        # Bucket: top-N by score vs the rest
        all_jobs.sort(key=_job_score, reverse=True)
        high = [j for j in all_jobs if _job_score(j) >= min_score][:top_n]
        # Fall back: if nothing crosses the bar, use the absolute top-N anyway
        # so we still produce useful output.
        if not high:
            high = all_jobs[:top_n]
        low = [j for j in all_jobs if j not in high]

        resume_skills = _resume_skill_set(profile)

        # Frequency of each token across HIGH jobs and LOW jobs.
        high_freq: Counter[str] = Counter()
        for j in high:
            for t in set(_tokenize(_job_text(j))):
                high_freq[t] += 1
        low_freq: Counter[str] = Counter()
        for j in low:
            for t in set(_tokenize(_job_text(j))):
                low_freq[t] += 1

        n_high = max(1, len(high))
        n_low = max(1, len(low))

        # "Differential" keyword: appears in many HIGH jobs but disproportionately
        # less in LOW jobs. high_rate - low_rate is the cheapest signal.
        ranked: list[tuple[str, float, int]] = []
        for tok, h in high_freq.items():
            if h < 2:
                continue  # noise
            high_rate = h / n_high
            low_rate = low_freq.get(tok, 0) / n_low
            score = high_rate - low_rate * 0.5  # prioritise high-set presence
            ranked.append((tok, score, h))
        ranked.sort(key=lambda x: x[1], reverse=True)

        top_missing = [
            {"keyword": t, "highJobCount": h, "score": round(s, 3)}
            for t, s, h in ranked
            if t not in resume_skills
        ][:15]
        top_matched = [
            {"keyword": t, "highJobCount": h, "score": round(s, 3)}
            for t, s, h in ranked
            if t in resume_skills
        ][:10]

        # Cheap headline suggestion: top-3 matched skills + top-2 missing
        # (ones the user likely has but didn't list explicitly).
        headline_terms = [x["keyword"] for x in top_matched[:3]] + [
            x["keyword"] for x in top_missing[:2]
        ]
        suggested_headline = ""
        if headline_terms:
            suggested_headline = (
                "Engineer specialising in "
                + ", ".join(headline_terms[:-1])
                + (" and " + headline_terms[-1] if len(headline_terms) > 1 else "")
            )

        return success_response({
            "totalJobsAnalysed": len(all_jobs),
            "highScoreCount": len(high),
            "lowScoreCount": len(low),
            "minScore": min_score,
            "topN": top_n,
            "topMissingKeywords": top_missing,
            "topMatchedKeywords": top_matched,
            "suggestedHeadline": suggested_headline,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Resume insights error")
        return internal_error_response(str(e))
