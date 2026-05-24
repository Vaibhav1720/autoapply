"""Offline tests guarding the two fixes for the
"product/design tag returned Software Engineer jobs" regression.

Both bugs are pure-Python (no network, no Cosmos, no LLM) so these tests
run in milliseconds and act as the canary the next time someone reaches
for the convenient `for fb in ("engineer", "developer")` shortcut.
"""

from __future__ import annotations

import os
import sys

import pytest

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from services.jobs.routes import _maybe_add_eng_fallbacks  # noqa: E402
from shared.career_scraper import _user_disciplines  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1: _maybe_add_eng_fallbacks
# ─────────────────────────────────────────────────────────────────────────────


class TestEngFallbacks:
    """Bug: every search was unconditionally widened with 'engineer' +
    'developer', polluting product/design/finance/healthcare etc. searches
    with Software Engineer postings that then dominated the matched top-N."""

    def test_design_search_not_polluted(self) -> None:
        qs = ["Product Designer", "UX Designer"]
        _maybe_add_eng_fallbacks(qs, industry="product_design")
        assert qs == ["Product Designer", "UX Designer"]

    def test_finance_search_not_polluted(self) -> None:
        qs = ["Investment Banking Analyst"]
        _maybe_add_eng_fallbacks(qs, industry="finance")
        assert qs == ["Investment Banking Analyst"]

    def test_healthcare_search_not_polluted(self) -> None:
        qs = ["Registered Nurse", "Pharmacist"]
        _maybe_add_eng_fallbacks(qs, industry="healthcare")
        assert qs == ["Registered Nurse", "Pharmacist"]

    def test_design_search_in_tech_industry_still_safe(self) -> None:
        # Even with a tech industry tag, a clean designer query must NOT
        # pull engineer/developer fallbacks.
        qs = ["Product Designer"]
        _maybe_add_eng_fallbacks(qs, industry="tech")
        assert qs == ["Product Designer"]

    def test_software_search_gets_fallbacks(self) -> None:
        qs = ["software"]
        _maybe_add_eng_fallbacks(qs, industry="tech")
        assert qs == ["software", "engineer", "developer"]

    def test_swe_search_appends_only_missing(self) -> None:
        qs = ["Software Engineer"]
        _maybe_add_eng_fallbacks(qs, industry="tech")
        assert "developer" in [q.lower() for q in qs]
        # 'engineer' alone is NOT in the list as a separate token; helper
        # appends it because the bare word doesn't match any existing query
        # exactly (case-insensitive substring is not the rule here).
        assert "engineer" in [q.lower() for q in qs]

    def test_no_industry_with_eng_query_gets_fallbacks(self) -> None:
        # Backwards-compat: legacy callers that don't pass an industry
        # should still work the same way for engineering queries.
        qs = ["backend"]
        _maybe_add_eng_fallbacks(qs)
        assert "engineer" in qs
        assert "developer" in qs

    def test_no_industry_with_design_query_no_fallbacks(self) -> None:
        # When industry is unknown but the query clearly isn't engineering,
        # don't widen with eng/dev fallbacks.
        qs = ["Product Designer"]
        _maybe_add_eng_fallbacks(qs)
        assert qs == ["Product Designer"]

    def test_idempotent(self) -> None:
        qs = ["software"]
        _maybe_add_eng_fallbacks(qs, industry="tech")
        snapshot = list(qs)
        _maybe_add_eng_fallbacks(qs, industry="tech")
        assert qs == snapshot


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2: _user_disciplines respects explicit industry
# ─────────────────────────────────────────────────────────────────────────────


class TestUserDisciplinesIndustryOverride:
    """Bug: <user> (SWE-flavored resume) tagged 'product/design' in the
    discover UI but the matcher inferred {backend, fullstack} from the
    resume and then the v20 filter dropped every Product Designer title
    as 'non-engineering noise'.

    Fix: explicit industry choice DOMINATES resume inference.
    """

    SWE_SKILLS = {"python", "java", "django", "kafka", "kubernetes", "react"}
    SWE_ROLES = ["software engineer", "senior software engineer"]

    def test_swe_resume_no_industry_stays_engineering(self) -> None:
        d = _user_disciplines(self.SWE_SKILLS, self.SWE_ROLES)
        assert "backend" in d  # from kafka/kubernetes/django
        # No industry hint -> resume wins -> still an engineer.

    def test_swe_resume_tech_industry_stays_engineering(self) -> None:
        d = _user_disciplines(self.SWE_SKILLS, self.SWE_ROLES, industry="tech")
        # tech maps to empty override -> resume inference applies.
        assert "backend" in d

    def test_swe_resume_product_design_overrides(self) -> None:
        d = _user_disciplines(self.SWE_SKILLS, self.SWE_ROLES, industry="product_design")
        assert d == {"product", "design"}

    def test_swe_resume_data_ai_overrides(self) -> None:
        d = _user_disciplines(self.SWE_SKILLS, self.SWE_ROLES, industry="data_ai")
        assert d == {"ml", "data"}

    def test_swe_resume_finance_overrides(self) -> None:
        d = _user_disciplines(self.SWE_SKILLS, self.SWE_ROLES, industry="finance")
        assert d == {"finance"}

    def test_unknown_industry_falls_back_to_resume(self) -> None:
        d = _user_disciplines(self.SWE_SKILLS, self.SWE_ROLES, industry="not_a_real_industry")
        assert "backend" in d  # fall through to resume inference

    def test_industry_case_insensitive(self) -> None:
        d = _user_disciplines(self.SWE_SKILLS, self.SWE_ROLES, industry="Product_Design")
        assert d == {"product", "design"}


# ─────────────────────────────────────────────────────────────────────────────
# Integration: matcher round-trip, no network
# ─────────────────────────────────────────────────────────────────────────────


def _swe_profile_with_industry(industry: str) -> dict:
    return {
        "email": "<your-admin-email>",
        "preferences": {
            "industry": industry,
            "experienceYears": 4,
            "locations": ["Bangalore"],
        },
        "skills": {"technical": ["Python", "Java", "Django", "React", "Kafka"]},
        "experience": [
            {"title": "Software Engineer", "company": "Acme", "from": "2021", "to": "2025"},
        ],
    }


def _job(jid: str, title: str, location: str = "Bangalore, India", skills: list[str] | None = None) -> dict:
    return {
        "id": jid,
        "company": "TestCo",
        "companyId": "comp-test",
        "title": title,
        "location": location,
        "url": f"https://example.test/{jid}",
        "skills": skills or [],
    }


def test_design_industry_keeps_designer_jobs_for_swe_profile() -> None:
    """End-to-end: with industry='product_design', the matcher must NOT
    drop Product Designer / UX Designer roles even though the resume is
    SWE-flavored. Pre-fix, these all got dropped by the v20 filter.

    NB: titles containing 'Manager' / 'Director' are independently dropped
    by the IC-seniority filter for a 4y profile with no management history,
    regardless of industry. That is desired behavior and orthogonal to this
    fix, so we test only IC-titled designer roles.
    """
    from shared.career_scraper import match_jobs_to_profile

    jobs = [
        _job("d1", "Product Designer"),
        _job("d2", "Senior Product Designer"),
        _job("d3", "UX Designer, Mobile"),
    ]
    profile = _swe_profile_with_industry("product_design")
    out = match_jobs_to_profile(jobs, profile)
    titles = [j["title"] for j in out]
    assert "Product Designer" in titles
    assert "UX Designer, Mobile" in titles
    assert "Senior Product Designer" in titles


def test_design_industry_drops_engineer_jobs_for_swe_profile() -> None:
    """Symmetric: with industry='product_design' the matcher SHOULD drop
    pure software-engineer titles via the v21 'non-eng-candidate eng filter'
    so the user actually sees their chosen discipline."""
    from shared.career_scraper import match_jobs_to_profile

    jobs = [
        _job("e1", "Software Engineer"),
        _job("e2", "Software Development Engineer II"),
        _job("e3", "Backend Engineer"),
        _job("d1", "Product Designer"),
    ]
    profile = _swe_profile_with_industry("product_design")
    out = match_jobs_to_profile(jobs, profile)
    titles = [j["title"] for j in out]
    assert "Product Designer" in titles
    assert "Software Engineer" not in titles
    assert "Backend Engineer" not in titles


def test_marketing_industry_keeps_marketing_manager_roles() -> None:
    """Marketing industry + SWE resume should surface Marketing Manager titles."""
    from shared.career_scraper import match_jobs_to_profile

    jobs = [
        _job("m1", "Marketing Manager"),
        _job("m2", "Brand Marketing Manager"),
        _job("e1", "Software Engineer"),
    ]
    profile = _swe_profile_with_industry("marketing")
    out = match_jobs_to_profile(jobs, profile)
    titles = [j["title"] for j in out]
    assert "Marketing Manager" in titles
    assert "Brand Marketing Manager" in titles
    assert "Software Engineer" not in titles


def test_pivot_marketing_search_for_swe_profile() -> None:
    """Off-resume LinkedIn search ('Marketing Manager') must not be zeroed by
    the engineering-candidate filter when pivot is detected."""
    from shared.career_scraper import match_jobs_to_profile

    jobs = [
        _job("m1", "Marketing Manager"),
        _job("m2", "Senior Marketing Manager"),
        _job("e1", "Software Engineer"),
    ]
    profile = _swe_profile_with_industry("tech")
    out = match_jobs_to_profile(
        jobs, profile,
        search_queries=["Marketing Manager"],
        pivot=True,
    )
    titles = [j["title"] for j in out]
    assert "Marketing Manager" in titles
    assert "Senior Marketing Manager" in titles
    assert "Software Engineer" not in titles


def test_tech_industry_swe_keeps_engineer_jobs_and_drops_hr() -> None:
    """Sanity: don't break the engineer-staying-as-engineer path.

    NB: 'Product Designer' deliberately survives this filter because
    `design` is NOT in `_PURE_NON_ENG_DISC` -- hybrid roles like
    'Design Engineer' / 'Product Design Engineer' are real titles an
    engineering candidate should see. The Product Designer job ranks
    low for a SWE profile via the discipline-mismatch skill penalty,
    but it's not hard-dropped. We verify the well-defined HR drop
    instead, which IS what the v20 filter exists for.
    """
    from shared.career_scraper import match_jobs_to_profile

    jobs = [
        _job("e1", "Software Engineer"),
        _job("e2", "Backend Engineer"),
        _job("r1", "Technical Recruiter"),
    ]
    profile = _swe_profile_with_industry("tech")
    out = match_jobs_to_profile(jobs, profile)
    titles = [j["title"] for j in out]
    assert "Software Engineer" in titles
    assert "Backend Engineer" in titles
    assert "Technical Recruiter" not in titles


# ─────────────────────────────────────────────────────────────────────────────
# Uber location flattener (caught while auditing the scraper)
# ─────────────────────────────────────────────────────────────────────────────


class TestUberLocationFlatten:
    """Uber's careers API returns `location` as either a string, a list of
    strings, a dict, or a list of dicts. Pre-fix, dicts were stringified
    via `str({...})` and ended up rendered as Python literals in the UI
    AND broke city matching downstream."""

    def test_string_passthrough(self) -> None:
        from shared.career_scraper import _flatten_uber_location
        assert _flatten_uber_location("Bangalore, India") == "Bangalore, India"

    def test_list_of_strings_takes_first(self) -> None:
        from shared.career_scraper import _flatten_uber_location
        assert _flatten_uber_location(["Sunnyvale, CA", "SF, CA"]) == "Sunnyvale, CA"

    def test_dict_flattens_to_csv(self) -> None:
        from shared.career_scraper import _flatten_uber_location
        d = {"country": "USA", "region": "California", "city": "Sunnyvale"}
        assert _flatten_uber_location(d) == "Sunnyvale, California, USA"

    def test_list_of_dicts_takes_first(self) -> None:
        from shared.career_scraper import _flatten_uber_location
        d = [{"city": "SF", "region": "CA", "country": "USA"},
             {"city": "Seattle", "region": "WA", "country": "USA"}]
        assert _flatten_uber_location(d) == "SF, CA, USA"

    def test_partial_dict_skips_missing(self) -> None:
        from shared.career_scraper import _flatten_uber_location
        assert _flatten_uber_location({"country": "USA"}) == "USA"

    def test_empty_returns_empty(self) -> None:
        from shared.career_scraper import _flatten_uber_location
        assert _flatten_uber_location("") == ""
        assert _flatten_uber_location(None) == ""
        assert _flatten_uber_location([]) == ""
        assert _flatten_uber_location({}) == ""
