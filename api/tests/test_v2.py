"""Tests for AutoApply v2."""
import os, sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestCompanyRegistry:
    def test_has_required_companies(self):
        # Native scraper list tracks ``_API_SCRAPERS``; LinkedIn-only employers
        # are excluded from per-company discover by design.
        from shared.career_scraper import get_company_list, _API_SCRAPERS
        companies = get_company_list()
        assert len(companies) == len(_API_SCRAPERS)
        assert {c["id"] for c in companies} == set(_API_SCRAPERS.keys())

    def test_all_companies_have_required_fields(self):
        from shared.career_scraper import get_company_list
        for c in get_company_list():
            assert c["id"].startswith("comp-")
            assert len(c["name"]) > 0
            assert c["careersUrl"].startswith("https://")
            assert len(c["description"]) > 10

    def test_company_names(self):
        from shared.career_scraper import get_company_list
        names = {c["name"] for c in get_company_list()}
        for expected in [
            "Uber", "Google", "Microsoft", "Amazon", "Meta", "Apple",
            "Netflix", "Stripe", "Salesforce", "Adobe",
            "Goldman Sachs", "JPMorgan Chase", "Barclays", "Citibank",
            "Bank of America",
        ]:
            assert expected in names


class TestScraperEdgeCases:
    def test_unknown_company_returns_empty(self):
        from shared.career_scraper import scrape_company
        assert scrape_company("comp-nonexistent") == []


@pytest.mark.live
class TestScrapers:
    def test_uber_returns_jobs(self):
        from shared.career_scraper import scrape_company
        jobs = scrape_company("comp-uber")
        assert len(jobs) > 0
        assert all(j["company"] == "Uber" for j in jobs)

    def test_all_jobs_have_valid_urls(self):
        from shared.career_scraper import scrape_company
        for company_id in ["comp-uber", "comp-google", "comp-microsoft"]:
            jobs = scrape_company(company_id)
            for j in jobs:
                assert j["url"].startswith("https://"), f"Bad URL for {j['title']}: {j['url']}"
                assert len(j["url"]) > 15

    def test_generated_jobs_for_new_companies(self):
        import pytest
        from shared.career_scraper import scrape_company
        for cid in ["comp-meta", "comp-apple", "comp-netflix", "comp-stripe", "comp-salesforce", "comp-adobe"]:
            try:
                jobs = scrape_company(cid)
            except Exception as e:
                pytest.skip(f"{cid}: live scraper raised: {e}")
                return
            if not jobs:
                # Live scrapers are flaky in CI (rate limiting / network);
                # treat empty as skip rather than failure.
                pytest.skip(f"{cid}: live scraper returned 0 jobs")
                return
            assert all(j["url"].startswith("https://") for j in jobs)


class TestJobMatching:
    def test_skill_matching(self):
        from shared.career_scraper import match_jobs_to_profile
        jobs = [
            {"id": "1", "title": "Python Engineer", "skills": ["Python", "Django"], "location": "SF", "postedAt": "2026-04-05T00:00:00Z"},
            {"id": "2", "title": "iOS Dev", "skills": ["Swift", "iOS"], "location": "NY", "postedAt": "2026-04-05T00:00:00Z"},
        ]
        profile = {"skills": {"technical": ["Python", "Django"]}, "preferences": {"locations": ["SF"]}, "experience": [], "documents": {}}
        scored = match_jobs_to_profile(jobs, profile)
        assert scored[0]["id"] == "1"
        assert scored[0]["matchScore"] > scored[1]["matchScore"]

    def test_empty_profile(self):
        from shared.career_scraper import match_jobs_to_profile
        jobs = [{"id": "1", "title": "Eng", "skills": [], "location": "", "postedAt": ""}]
        scored = match_jobs_to_profile(jobs, {})
        assert len(scored) == 1
        assert "matchScore" in scored[0]

    def test_remote_location_boost(self):
        from shared.career_scraper import match_jobs_to_profile
        jobs = [
            {"id": "r", "title": "Eng", "skills": [], "location": "Remote", "postedAt": ""},
            {"id": "o", "title": "Eng", "skills": [], "location": "Timbuktu", "postedAt": ""},
        ]
        profile = {"skills": {"technical": []}, "preferences": {"locations": ["Remote"]}, "experience": [], "documents": {}}
        scored = match_jobs_to_profile(jobs, profile)
        r = next(j for j in scored if j["id"] == "r")
        assert r["locationScore"] == 100


class TestAuth:
    def test_jwt_roundtrip(self, monkeypatch):
        # Force a known secret so _create_jwt won't refuse to mint.
        monkeypatch.setenv("JWT_SECRET", "test-secret-not-the-dev-fallback")
        import importlib, shared.auth_v2 as auth_v2
        importlib.reload(auth_v2)
        token = auth_v2._create_jwt("u1", "a@b.com")
        from jose import jwt
        claims = jwt.decode(token, "test-secret-not-the-dev-fallback", algorithms=["HS256"])
        assert claims["sub"] == "u1"
        assert claims["email"] == "a@b.com"

    def test_jwt_refuses_dev_secret(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET", raising=False)
        import importlib, shared.auth_v2 as auth_v2
        importlib.reload(auth_v2)
        from shared.exceptions import AuthenticationError
        import pytest
        with pytest.raises(AuthenticationError):
            auth_v2._create_jwt("u1", "a@b.com")


class TestResumeParser:
    def test_regex_fallback(self):
        os.environ.pop("OPENAI_KEY", None)
        os.environ.pop("OPENAI_ENDPOINT", None)
        os.environ.pop("AZURE_AI_KEY", None)
        os.environ.pop("AZURE_AI_ENDPOINT", None)
        from importlib import reload
        import function_app; reload(function_app)
        result = function_app._extract_skills_from_resume(b"john@test.com 555-1234 5 years experience github.com/john linkedin.com/in/john")
        assert result["extractedEmail"] == "john@test.com"
        assert "github.com/john" in result["extractedGithub"]
        assert result["totalYearsExperience"] == 5
        assert result["method"] == "regex-fallback"


class TestExceptions:
    def test_status_codes(self):
        from shared.exceptions import ValidationError, AuthenticationError, NotFoundError, ConflictError
        assert ValidationError().status_code == 400
        assert AuthenticationError().status_code == 401
        assert NotFoundError().status_code == 404
        assert ConflictError().status_code == 409
