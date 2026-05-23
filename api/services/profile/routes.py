"""Profile — get/update, resume upload, missing-info, application-details, resume review."""

import os
from datetime import datetime, timezone

import azure.functions as func

from shared.auth_v2 import get_user_id
from shared.blob_client import upload_blob
from shared.cosmos_client import read_item, upsert_item
from shared.embeddings import (
    generate_embedding,
    generate_profile_summary,
    profile_to_text,
)
from shared.exceptions import (
    AppException,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from shared.response_helpers import (
    created_response,
    error_response,
    internal_error_response,
    success_response,
)
from services._runtime import (
    AI_REVIEW_MODEL,
    _COMMON_QUESTIONS,
    _OPTIONAL_QUESTION_KEYS,
    _check_daily_resume_upload_quota,
    _extract_multipart_file,
    _extract_skills_from_resume,
    _is_modern_model,
    _require_pro_or_paid_resume_review,
    get_country_for_billing,
    get_usage_summary,
    get_upgrade_message,
    logger,
)
from shared.exceptions import RateLimitError

bp = func.Blueprint()


@bp.route(route="api/v1/profile", methods=["GET"])
def get_profile(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/v1/profile — Get current user profile."""
    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")
        profile.pop("profileEmbedding", None)
        profile.pop("_rid", None)
        profile.pop("_self", None)
        profile.pop("_etag", None)
        profile.pop("_attachments", None)
        profile.pop("_ts", None)
        return success_response(profile)
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error getting profile")
        return internal_error_response(str(e))


@bp.route(route="api/v1/profile/usage", methods=["GET"])
def get_profile_usage(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/v1/profile/usage — Return daily usage counts and free-tier limits."""
    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")
        return success_response(get_usage_summary(profile, req=req))
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error getting usage")
        return internal_error_response(str(e))


@bp.route(route="api/v1/profile", methods=["PUT"])
def update_profile(req: func.HttpRequest) -> func.HttpResponse:
    """PUT /api/v1/profile — Update profile fields (partial merge)."""
    try:
        user_id = get_user_id(req)
        existing = read_item("profiles", user_id, user_id)
        if not existing:
            raise NotFoundError("Profile not found")

        updates = req.get_json()
        for key, value in updates.items():
            if key in ("id", "userId", "type", "createdAt"):
                continue
            if isinstance(value, dict) and isinstance(existing.get(key), dict):
                existing[key].update(value)
            else:
                existing[key] = value

        existing["updatedAt"] = datetime.now(timezone.utc).isoformat()

        if any(k in updates for k in ("skills", "experience", "education", "preferences")):
            ai_summary = generate_profile_summary(existing)
            if ai_summary:
                existing["aiSummary"] = ai_summary
            profile_text = profile_to_text(existing)
            if profile_text:
                existing["profileEmbedding"] = generate_embedding(profile_text)
                logger.info("[PROFILE] Updated summary + embedding (%d dims)",
                            len(existing.get("profileEmbedding", [])))

        updated = upsert_item("profiles", existing)
        return success_response(updated)
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error updating profile")
        return internal_error_response(str(e))


@bp.route(route="api/v1/profile/resume", methods=["POST"])
def upload_resume(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/profile/resume — Upload resume PDF + parse skills."""
    try:
        import base64 as b64mod

        user_id = get_user_id(req)
        logger.info("[UPLOAD_RESUME] User: %s, Content-Type: %s", user_id, req.headers.get("Content-Type", ""))
        file_data = req.get_body()
        logger.info("[UPLOAD_RESUME] Raw body size: %d bytes", len(file_data))

        content_type = req.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                body = req.get_json()
                if body.get("fileBase64"):
                    file_data = b64mod.b64decode(body["fileBase64"])
                    logger.info("[UPLOAD_RESUME] Decoded base64 file, size: %d bytes", len(file_data))
            except Exception as e:
                logger.warning("[UPLOAD_RESUME] Failed to decode base64: %s", e)

        if "multipart/form-data" in content_type:
            file_data = _extract_multipart_file(file_data, content_type)
            logger.info("[UPLOAD_RESUME] Extracted multipart file, size: %d bytes", len(file_data))

        if not file_data:
            raise ValidationError("No file data provided")
        if len(file_data) > 10 * 1024 * 1024:
            raise ValidationError("File too large (max 10MB)")

        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        version = (profile.get("documents") or {}).get("resumeVersion", 0) + 1
        blob_name = f"{user_id}/resume_v{version}.pdf"
        resume_url = upload_blob("resumes", blob_name, file_data, "application/pdf")

        logger.info("[UPLOAD_RESUME] Starting AI resume parsing...")
        parsed = _extract_skills_from_resume(file_data)
        parsed_skills = parsed.get("extractedSkills", [])
        logger.info("[UPLOAD_RESUME] Parse method: %s", parsed.get("method", "unknown"))
        logger.info("[UPLOAD_RESUME] Parsed skills (%d): %s", len(parsed_skills), parsed_skills)

        docs = profile.get("documents") or {}
        docs["resumeUrl"] = resume_url
        docs["resumeVersion"] = version
        docs["parsedResumeData"] = parsed
        profile["documents"] = docs

        existing_skills_obj = profile.get("skills") or {"technical": [], "soft": []}
        prev_skills = list(existing_skills_obj.get("technical", []))
        seen_lc: set[str] = set()
        cleaned_skills: list[str] = []
        for s in parsed_skills:
            if not s:
                continue
            lc = s.strip().lower()
            if lc and lc not in seen_lc:
                seen_lc.add(lc)
                cleaned_skills.append(s.strip())
        logger.info("[UPLOAD_RESUME] Replacing technical skills: %d -> %d (prev=%s, new=%s)",
                    len(prev_skills), len(cleaned_skills), prev_skills, cleaned_skills)
        existing_skills_obj["technical"] = cleaned_skills
        profile["skills"] = existing_skills_obj

        parsed_edu = parsed.get("extractedEducation", [])
        logger.info("[UPLOAD_RESUME] Parsed education (%d): %s", len(parsed_edu), parsed_edu)
        if parsed_edu:
            profile["education"] = parsed_edu

        parsed_exp = parsed.get("extractedExperience", [])
        logger.info("[UPLOAD_RESUME] Parsed experience (%d): %s", len(parsed_exp), parsed_exp)
        if parsed_exp:
            profile["experience"] = parsed_exp

        personal = profile.get("personal") or {}
        if parsed.get("extractedEmail") and not profile.get("email"):
            profile["email"] = parsed["extractedEmail"]
        if parsed.get("extractedPhone") and not personal.get("phone"):
            personal["phone"] = parsed["extractedPhone"]
        if parsed.get("extractedLinkedin") and not profile.get("linkedinUrl"):
            profile["linkedinUrl"] = parsed["extractedLinkedin"]
        if parsed.get("extractedGithub") and not personal.get("githubUrl"):
            personal["githubUrl"] = parsed["extractedGithub"]
        if parsed.get("extractedPortfolio") and not personal.get("portfolioUrl"):
            personal["portfolioUrl"] = parsed["extractedPortfolio"]
        if parsed.get("extractedFirstName") and not personal.get("firstName"):
            personal["firstName"] = parsed["extractedFirstName"]
        if parsed.get("extractedLastName") and not personal.get("lastName"):
            personal["lastName"] = parsed["extractedLastName"]
        profile["personal"] = personal

        app_details = profile.get("applicationDetails") or {}
        for resume_key, profile_key in [
            ("extractedAddress", "address"), ("extractedCity", "city"),
            ("extractedState", "state"), ("extractedCountry", "country"),
            ("extractedZip", "zip"),
        ]:
            if parsed.get(resume_key) and not app_details.get(profile_key):
                app_details[profile_key] = parsed[resume_key]
        if parsed.get("extractedCoverLetter") and not app_details.get("coverLetter"):
            app_details["coverLetter"] = parsed["extractedCoverLetter"]
        profile["applicationDetails"] = app_details

        profile["updatedAt"] = datetime.now(timezone.utc).isoformat()

        ai_summary = generate_profile_summary(profile)
        if ai_summary:
            profile["aiSummary"] = ai_summary
            logger.info("[UPLOAD_RESUME] AI summary: %s", ai_summary[:100])

        profile_text = profile_to_text(profile)
        if profile_text:
            profile["profileEmbedding"] = generate_embedding(profile_text)
            logger.info("[UPLOAD_RESUME] Generated profile embedding (%d dims)",
                        len(profile.get("profileEmbedding", [])))

        upsert_item("profiles", profile)

        final_skills = (profile.get("skills") or {}).get("technical", [])
        logger.info("[UPLOAD_RESUME] === FINAL RESULT ===")
        logger.info("[UPLOAD_RESUME] Total skills in profile: %d -> %s", len(final_skills), final_skills)
        logger.info("[UPLOAD_RESUME] Education added: %d", len(parsed_edu))
        logger.info("[UPLOAD_RESUME] Experience added: %d", len(parsed_exp))
        logger.info("[UPLOAD_RESUME] Parse method: %s", parsed.get("method", "unknown"))

        return success_response({
            "resumeUrl": resume_url,
            "version": version,
            "extractedSkills": parsed_skills,
            "extractedEducation": parsed_edu,
            "extractedExperience": parsed_exp,
            "message": (
                f"Resume uploaded. Extracted {len(parsed_skills)} skills"
                f"{f', {len(parsed_edu)} education entries' if parsed_edu else ''}"
                f"{f', {len(parsed_exp)} experience entries' if parsed_exp else ''}."
            ),
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error uploading resume")
        return internal_error_response(str(e))


@bp.route(route="api/v1/profile/missing-info", methods=["GET"])
def get_missing_info(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/v1/profile/missing-info — common application questions still empty."""
    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        personal = profile.get("personal") or {}
        app_details = profile.get("applicationDetails") or {}

        getters = {
            "linkedinUrl": profile.get("linkedinUrl", ""),
            "githubUrl": personal.get("githubUrl", ""),
            "portfolioUrl": personal.get("portfolioUrl", ""),
        }

        missing = []
        for q in _COMMON_QUESTIONS:
            key = q["key"]
            if key in _OPTIONAL_QUESTION_KEYS:
                continue
            current = getters.get(key, app_details.get(key, ""))
            if not (current and str(current).strip()):
                missing.append(q)

        name_missing = []
        if not personal.get("firstName"):
            name_missing.append({"key": "firstName", "label": "First name", "type": "text"})
        if not personal.get("lastName"):
            name_missing.append({"key": "lastName", "label": "Last name", "type": "text"})
        if not personal.get("phone"):
            name_missing.append({"key": "phone", "label": "Phone number", "type": "text"})

        required_count = sum(1 for q in _COMMON_QUESTIONS if q["key"] not in _OPTIONAL_QUESTION_KEYS) + 3
        return success_response({
            "missing": name_missing + missing,
            "totalCommon": required_count,
            "completeness": round(
                100 * (required_count - len(name_missing) - len(missing)) / required_count
            ),
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error fetching missing info")
        return internal_error_response(str(e))


@bp.route(route="api/v1/profile/application-details", methods=["PUT"])
def update_application_details(req: func.HttpRequest) -> func.HttpResponse:
    """PUT /api/v1/profile/application-details — Save additional details for job apps."""
    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        details = req.get_json()
        allowed = {
            "address", "city", "state", "zip", "country",
            "coverLetter", "salaryExpectation", "noticePeriod",
            "visaStatus", "willingToRelocate", "remoteWork", "gender",
            "veteranStatus", "disability", "ethnicity",
        }
        filtered = {k: v for k, v in details.items() if k in allowed}

        existing_details = profile.get("applicationDetails") or {}
        existing_details.update(filtered)

        if isinstance(details.get("customAnswers"), dict):
            cleaned = {}
            for k, v in details["customAnswers"].items():
                if not isinstance(v, dict):
                    continue
                label = str(v.get("label", "")).strip()[:200]
                value = str(v.get("value", "")).strip()[:2000]
                key = str(k).strip()[:120]
                if key and label and value:
                    cleaned[key] = {"label": label, "value": value}
            existing_details["customAnswers"] = cleaned

        profile["applicationDetails"] = existing_details

        personal = profile.get("personal") or {}
        for k in ("firstName", "lastName", "phone", "githubUrl", "portfolioUrl"):
            if details.get(k):
                personal[k] = str(details[k]).strip()
        profile["personal"] = personal
        if details.get("linkedinUrl"):
            profile["linkedinUrl"] = str(details["linkedinUrl"]).strip()

        profile["updatedAt"] = datetime.now(timezone.utc).isoformat()
        upsert_item("profiles", profile)

        return success_response(existing_details)
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error updating application details")
        return internal_error_response(str(e))


@bp.route(route="api/v1/resume/review", methods=["POST"])
def request_resume_review(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/resume/review — Paid AI + human resume review intake."""
    try:
        user_id = get_user_id(req)
        body = req.get_json() if req.get_body() else {}
        target_role = (body.get("targetRole") or "").strip()
        user_notes = (body.get("notes") or "").strip()

        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        _require_pro_or_paid_resume_review(profile)

        ai_endpoint = os.environ.get("AZURE_AI_ENDPOINT", os.environ.get("OPENAI_ENDPOINT", ""))
        ai_key = os.environ.get("AZURE_AI_KEY", os.environ.get("OPENAI_KEY", ""))
        ai_critique = ""
        if ai_key and ai_endpoint:
            try:
                import openai
                client = openai.AzureOpenAI(api_key=ai_key, api_version="2024-12-01-preview", azure_endpoint=ai_endpoint)
                resume_text = profile_to_text(profile)
                prompt = (
                    "You are a senior tech recruiter and career coach. Critique this resume in 5 sections: "
                    "(1) Top 3 strengths, (2) Top 5 weaknesses, (3) Missing keywords for "
                    f"target role '{target_role or 'general SDE roles'}', (4) Specific bullet rewrites for the weakest 3 bullets, "
                    "(5) Overall ATS score 1-100 with rationale. Be brutally specific. Return Markdown.\n\n"
                    f"USER NOTES: {user_notes or '(none)'}\n\nRESUME:\n{resume_text}"
                )
                kwargs = {"model": AI_REVIEW_MODEL, "messages": [{"role": "user", "content": prompt}]}
                if _is_modern_model(AI_REVIEW_MODEL):
                    kwargs["max_completion_tokens"] = 12000
                    if AI_REVIEW_MODEL.lower().replace("-", "").startswith("gpt5"):
                        kwargs["reasoning_effort"] = "low"
                else:
                    kwargs["max_tokens"] = 2500
                    kwargs["temperature"] = 0.3
                resp = client.chat.completions.create(**kwargs)
                ai_critique = resp.choices[0].message.content or ""
            except Exception as e:
                logger.warning("[RESUME_REVIEW] AI critique failed: %s", e)
                ai_critique = "AI first-pass unavailable; human reviewer will assess."

        review_id = f"rev-{user_id}-{int(datetime.now(timezone.utc).timestamp())}"
        record = {
            "id": review_id,
            "userId": user_id,
            "email": profile.get("email", ""),
            "targetRole": target_role,
            "userNotes": user_notes,
            "aiCritique": ai_critique,
            "humanReview": None,
            "status": "pending_human",
            "paymentStatus": "unverified",
            "tier": "standard",
            "priceInr": 499,
            "priceUsd": 19,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            upsert_item("applications", record)
        except Exception as e:
            logger.warning("[RESUME_REVIEW] Persist failed: %s", e)

        return created_response({
            "reviewId": review_id,
            "status": "pending_human",
            "aiCritique": ai_critique,
            "etaHours": 48,
            "message": "AI first-pass complete. A human reviewer will follow up within 48h.",
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Resume review error")
        return internal_error_response(str(e))
