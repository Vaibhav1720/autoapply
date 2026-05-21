"""Companies — list, select, get-selected."""

import os
from datetime import datetime, timezone

import azure.functions as func

from shared.auth_v2 import get_user_id
from shared.career_scraper import get_company_list
from shared.cosmos_client import read_item, upsert_item
from shared.exceptions import AppException, NotFoundError, ValidationError
from shared.response_helpers import (
    error_response,
    internal_error_response,
    success_response,
)
from services._runtime import logger

bp = func.Blueprint()


@bp.route(route="api/v1/companies", methods=["GET"])
def list_companies(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/v1/companies — List available companies."""
    try:
        companies = get_company_list()
        return success_response({"companies": companies})
    except Exception as e:
        logger.exception("Error listing companies")
        return internal_error_response(str(e))


@bp.route(route="api/v1/companies/select", methods=["POST"])
def select_companies(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/v1/companies/select — Save user's selected companies."""
    try:
        user_id = get_user_id(req)
        body = req.get_json()
        company_ids = body.get("companyIds", [])

        if not company_ids:
            raise ValidationError("companyIds is required (list of company IDs)")

        _MAX_SELECTED = int(os.environ.get("MAX_SELECTED_COMPANIES", "200"))
        if len(company_ids) > _MAX_SELECTED:
            raise ValidationError(
                f"You can select at most {_MAX_SELECTED} companies. "
                f"You picked {len(company_ids)} — please remove some.")

        valid_ids = {c["id"] for c in get_company_list()}
        invalid = [cid for cid in company_ids if cid not in valid_ids]
        if invalid:
            raise ValidationError(f"Invalid company IDs: {invalid}")

        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        profile["selectedCompanies"] = company_ids
        profile["updatedAt"] = datetime.now(timezone.utc).isoformat()
        upsert_item("profiles", profile)

        return success_response({
            "selected": company_ids,
            "count": len(company_ids),
        })
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error selecting companies")
        return internal_error_response(str(e))


@bp.route(route="api/v1/companies/selected", methods=["GET"])
def get_selected_companies(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/v1/companies/selected — Get user's selected companies."""
    try:
        user_id = get_user_id(req)
        profile = read_item("profiles", user_id, user_id)
        if not profile:
            raise NotFoundError("Profile not found")

        selected_ids = profile.get("selectedCompanies", [])
        all_companies = {c["id"]: c for c in get_company_list()}
        selected = [all_companies[cid] for cid in selected_ids if cid in all_companies]

        return success_response({"selected": selected, "count": len(selected)})
    except AppException as e:
        return error_response(e)
    except Exception as e:
        logger.exception("Error getting selected companies")
        return internal_error_response(str(e))
