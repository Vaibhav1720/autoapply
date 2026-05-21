"""Auth — Google sign-in + account deletion."""

import azure.functions as func

from shared.auth_v2 import get_user_id, login_with_google
from shared.blob_client import delete_blob
from shared.cosmos_client import delete_item, read_item
from shared.exceptions import AppException, ValidationError
from shared.response_helpers import (
    error_response,
    internal_error_response,
    success_response,
)
from services._runtime import logger

bp = func.Blueprint()


@bp.route(route="api/v1/auth/google", methods=["POST"])
def auth_google(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/auth/google — Sign in with a Google ID token."""
    try:
        body = req.get_json() or {}
        result = login_with_google(id_token_str=body.get("idToken", ""))
        return success_response(result)
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Google login error")
        return internal_error_response(str(e))


@bp.route(route="api/v1/account", methods=["DELETE"])
def delete_account(req: func.HttpRequest) -> func.HttpResponse:
    """DELETE /api/v1/account — Permanently delete the user's account."""
    try:
        user_id = get_user_id(req)

        try:
            body = req.get_json() or {}
        except Exception:
            body = {}
        if (body.get("confirm") or "").strip().upper() != "DELETE":
            raise ValidationError('Account deletion requires {"confirm": "DELETE"} in the body.')

        deleted = {"resumes": 0, "profile": False, "jobResults": False, "user": False}

        try:
            profile = read_item("profiles", user_id, user_id) or {}
            docs = profile.get("documents") or {}
            latest_version = int(docs.get("resumeVersion") or 0)
            for v in range(1, latest_version + 1):
                try:
                    delete_blob("resumes", f"{user_id}/resume_v{v}.pdf")
                    deleted["resumes"] += 1
                except Exception as be:
                    logger.warning("delete_account: blob v%s skipped: %s", v, be)
        except Exception as e:
            logger.warning("delete_account: could not enumerate resumes: %s", e)

        try:
            delete_item("profiles", user_id, user_id)
            deleted["profile"] = True
        except Exception as e:
            logger.warning("delete_account: profile delete skipped: %s", e)
        try:
            delete_item("job_results", f"results-{user_id}", user_id)
            deleted["jobResults"] = True
        except Exception as e:
            logger.warning("delete_account: job_results delete skipped: %s", e)
        try:
            delete_item("users", user_id, user_id)
            deleted["user"] = True
        except Exception as e:
            logger.warning("delete_account: user delete skipped: %s", e)

        return success_response({"deleted": True, "summary": deleted})
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Account deletion error")
        return internal_error_response(str(e))
