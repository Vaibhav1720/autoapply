"""Autofill — Chrome extension flat profile, AI suggestions, custom answer memory."""

import azure.functions as func

from shared.auth_v2 import get_user_id
from shared.cosmos_client import read_item, upsert_item
from shared.exceptions import AppException, NotFoundError, RateLimitError, ValidationError
from shared.response_helpers import (
    error_response,
    internal_error_response,
    success_response,
)
from services._runtime import (
    get_upgrade_message,
    get_country_for_billing,
    _check_daily_autofill_quota,
    _expand_country,
    _normalize_label,
    _suggest_answers,
    logger,
)

bp = func.Blueprint()


@bp.route(route="api/v1/autofill/profile", methods=["GET"])
def autofill_profile(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/v1/autofill/profile — Flat profile for the browser extension."""
    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        personal = profile.get("personal") or {}
        skills = (profile.get("skills") or {}).get("technical", [])
        prefs = profile.get("preferences") or {}
        experience = profile.get("experience") or []
        education = profile.get("education") or []
        app_details = profile.get("applicationDetails") or {}

        latest_exp = experience[0] if experience and isinstance(experience[0], dict) else {}
        latest_edu = education[0] if education and isinstance(education[0], dict) else {}

        raw_country = (app_details.get("country", "") or "").strip()
        country_full = _expand_country(raw_country)

        flat = {
            "firstName": personal.get("firstName", ""),
            "lastName": personal.get("lastName", ""),
            "fullName": f"{personal.get('firstName', '')} {personal.get('lastName', '')}".strip(),
            "email": profile.get("email", ""),
            "phone": personal.get("phone", ""),
            "linkedinUrl": profile.get("linkedinUrl", ""),
            "githubUrl": personal.get("githubUrl", ""),
            "portfolioUrl": personal.get("portfolioUrl", ""),
            "address": app_details.get("address", ""),
            "city": app_details.get("city", ""),
            "state": app_details.get("state", ""),
            "zip": app_details.get("zip", ""),
            "country": country_full,
            "countryCode": raw_country.upper() if len(raw_country) <= 3 else "",
            "skills": ", ".join(skills[:30]),
            "summary": profile.get("aiSummary", ""),
            "coverLetter": app_details.get("coverLetter", ""),
            "currentTitle": latest_exp.get("title", ""),
            "currentCompany": latest_exp.get("company", ""),
            "experienceYears": str(prefs.get("experienceYears", "")),
            "degree": latest_edu.get("degree", ""),
            "university": latest_edu.get("university", ""),
            "graduationYear": str(latest_edu.get("year", "")),
            "salaryExpectation": app_details.get("salaryExpectation", ""),
            "noticePeriod": app_details.get("noticePeriod", ""),
            "visaStatus": app_details.get("visaStatus", ""),
            "willingToRelocate": app_details.get("willingToRelocate", ""),
            "gender": app_details.get("gender", ""),
            "veteranStatus": app_details.get("veteranStatus", ""),
            "disability": app_details.get("disability", ""),
            "ethnicity": app_details.get("ethnicity", ""),
            "experience": experience[:5],
            "education": education[:3],
        }
        return success_response(flat)
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error getting autofill profile")
        return internal_error_response(str(e))


@bp.route(route="api/v1/autofill/suggest", methods=["POST"])
def autofill_suggest(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/autofill/suggest — AI-powered field suggestions."""
    try:
        user_id = get_user_id(req)
        body = req.get_json()
        fields = body.get("fields", [])
        if not fields:
            raise ValidationError("fields is required")
        if len(fields) > 30:
            raise ValidationError("Maximum 30 fields per request")

        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        # ── Free-tier AI autofill quota ──
        allowed, remaining = _check_daily_autofill_quota(profile)
        if not allowed:
            country = get_country_for_billing(req, profile)
            raise RateLimitError(get_upgrade_message(country))

        answers = _suggest_answers(profile, fields)
        return success_response({"answers": answers})
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error suggesting autofill answers")
        return internal_error_response(str(e))


@bp.route(route="api/v1/autofill/save-answers", methods=["POST"])
def autofill_save_answers(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/autofill/save-answers — Persist user-supplied answers."""
    try:
        user_id = get_user_id(req)
        body = req.get_json() or {}
        answers = body.get("answers") or []
        if not isinstance(answers, list) or not answers:
            raise ValidationError("answers must be a non-empty list")
        if len(answers) > 50:
            raise ValidationError("Maximum 50 answers per request")

        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        app_details = profile.get("applicationDetails") or {}
        custom = app_details.get("customAnswers") or {}

        saved = 0
        for a in answers:
            label = str(a.get("label", "")).strip()
            value = str(a.get("value", "")).strip()
            if not label or not value:
                continue
            key = _normalize_label(label)
            if not key:
                continue
            custom[key] = {"label": label[:200], "value": value[:2000]}
            saved += 1

        if saved == 0:
            return success_response({"saved": 0})

        app_details["customAnswers"] = custom
        profile["applicationDetails"] = app_details
        upsert_item("profiles", profile)
        return success_response({"saved": saved, "totalRemembered": len(custom)})
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error saving custom autofill answers")
        return internal_error_response(str(e))
