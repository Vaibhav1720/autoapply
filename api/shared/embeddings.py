"""Embedding helpers — generate and compare embeddings via Azure AI Foundry."""

import os
import math
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# text-embedding-3-large produces 3072-dim vectors (vs 1536 for small)
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMS = 3072

_client = None


def _get_client():
    """Lazy-init Azure OpenAI client (works with AI Foundry / AIServices)."""
    global _client
    if _client is None:
        import openai
        _client = openai.AzureOpenAI(
            api_key=os.environ.get("AZURE_AI_KEY", os.environ.get("OPENAI_KEY", "")),
            api_version="2024-12-01-preview",
            azure_endpoint=os.environ.get("AZURE_AI_ENDPOINT", os.environ.get("OPENAI_ENDPOINT", "")),
        )
    return _client


def generate_embedding(text: str, model: str | None = None) -> list[float]:
    """Generate an embedding for a text string.

    Default model is text-embedding-3-large (3072-dim) for matching/scoring.
    Pass `model="text-embedding-3-small"` for cheap classification work
    where 1536-dim is plenty.
    """
    if not text or len(text.strip()) < 5:
        return []
    try:
        client = _get_client()
        resp = client.embeddings.create(
            model=model or EMBEDDING_MODEL,
            input=text[:8000],  # model limit
        )
        return resp.data[0].embedding
    except Exception as e:
        logger.warning("[EMBEDDING] Failed to generate embedding: %s", e)
        return []


def generate_embeddings_batch(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Generate embeddings for multiple texts in a single API call.

    See generate_embedding() for the optional `model` override.
    """
    if not texts:
        return []
    try:
        client = _get_client()
        # API supports up to 2048 inputs, trim each to 8000 chars
        trimmed = [t[:8000] for t in texts if t and len(t.strip()) >= 5]
        if not trimmed:
            return [[] for _ in texts]
        resp = client.embeddings.create(
            model=model or EMBEDDING_MODEL,
            input=trimmed,
        )
        embeddings = [d.embedding for d in resp.data]
        # Map back to original indices (fill empty for skipped texts)
        result = []
        emb_idx = 0
        for t in texts:
            if t and len(t.strip()) >= 5:
                result.append(embeddings[emb_idx] if emb_idx < len(embeddings) else [])
                emb_idx += 1
            else:
                result.append([])
        return result
    except Exception as e:
        logger.warning("[EMBEDDING] Batch embedding failed: %s", e)
        return [[] for _ in texts]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors. Returns 0-1."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def generate_profile_summary(profile: dict) -> str:
    """Use AI to generate a professional summary optimized for job matching."""
    try:
        client = _get_client()

        skills = (profile.get("skills") or {}).get("technical", [])
        experience = profile.get("experience") or []
        education = profile.get("education") or []
        prefs = profile.get("preferences") or {}

        exp_text = "\n".join(
            f"- {e.get('title','')} at {e.get('company','')} ({e.get('from','')}-{e.get('to','')})"
            for e in experience[:5] if isinstance(e, dict))
        edu_text = "\n".join(
            f"- {e.get('degree','')} from {e.get('university','')} ({e.get('year','')})"
            for e in education[:3] if isinstance(e, dict))

        prompt = (
            f"Create a concise professional summary (3-4 sentences) for this candidate, "
            f"optimized for matching against job descriptions. Focus on their core technical "
            f"expertise, domain experience, seniority level, and what type of roles suit them best.\n\n"
            f"Skills: {', '.join(skills[:30])}\n"
            f"Experience:\n{exp_text}\n"
            f"Education:\n{edu_text}\n"
            f"Years of experience: {prefs.get('experienceYears', 'unknown')}\n"
            f"Looking for: {', '.join(prefs.get('keywords', []))}\n\n"
            f"Write ONLY the summary, no headers or formatting."
        )

        resp = client.chat.completions.create(
            model="gpt41",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300, temperature=0)

        summary = resp.choices[0].message.content.strip()
        logger.info("[PROFILE_SUMMARY] Generated: %s", summary[:100])
        return summary
    except Exception as e:
        logger.warning("[PROFILE_SUMMARY] Failed: %s", e)
        return ""


def profile_to_text(profile: dict) -> str:
    """Convert a user profile to a rich text string for embedding."""
    parts = []

    # AI-generated summary (highest priority — best semantic representation)
    summary = profile.get("aiSummary", "")
    if summary:
        parts.append(f"Professional summary: {summary}")

    # Skills (high priority)
    skills = (profile.get("skills") or {}).get("technical", [])
    if skills:
        parts.append(f"Technical skills: {', '.join(skills[:40])}")

    # Experience with titles AND companies (very high priority for matching)
    for exp in (profile.get("experience") or [])[:5]:
        if isinstance(exp, dict):
            title = exp.get('title', '')
            company = exp.get('company', '')
            from_date = exp.get('from', '')
            to_date = exp.get('to', '')
            if title:
                parts.append(f"Worked as {title} at {company} from {from_date} to {to_date}")

    # Parsed resume data (if available - contains richer info)
    parsed = (profile.get("documents") or {}).get("parsedResumeData", {})
    if parsed:
        extracted_skills = parsed.get("extractedSkills", [])
        if extracted_skills:
            # Add any skills not already in the main skills list
            existing = set(s.lower() for s in skills)
            extra = [s for s in extracted_skills if s.lower() not in existing]
            if extra:
                parts.append(f"Additional skills from resume: {', '.join(extra[:20])}")

    # Education
    for edu in (profile.get("education") or [])[:3]:
        if isinstance(edu, dict):
            parts.append(f"{edu.get('degree', '')} from {edu.get('university', '')} ({edu.get('year', '')})")

    # Keywords
    prefs = profile.get("preferences") or {}
    kw = prefs.get("keywords", [])
    if kw:
        parts.append(f"Looking for roles: {', '.join(kw)}")

    if prefs.get("locations"):
        parts.append(f"Preferred locations: {', '.join(prefs['locations'])}")
    if prefs.get("experienceYears"):
        parts.append(f"Total experience: {prefs['experienceYears']} years")

    return ". ".join(parts)


def job_to_text(job: dict) -> str:
    """Convert a job listing to a rich text string for embedding."""
    parts = [job.get("title", "")]
    if job.get("company"):
        parts.append(f"at {job['company']}")
    if job.get("location"):
        parts.append(f"in {job['location']}")
    if job.get("skills"):
        parts.append(f"requiring {', '.join(job['skills'][:10])}")
    # Include any description snippet if available
    if job.get("description"):
        parts.append(job["description"][:200])
    return " ".join(parts)
