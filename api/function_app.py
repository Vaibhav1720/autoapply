"""AutoApply v2 — Azure Functions app entrypoint.

This file used to contain ~2900 lines of monolithic logic. It now does just
two things:

1. Construct the shared FunctionApp.
2. Register one func.Blueprint per service (auth, profile, companies, jobs,
   autofill, health/admin). Each blueprint lives under api/services/<domain>/
   and owns its routes + handlers.

The previous monolith is preserved at function_app.py.bak_pre_split so we
can diff easily during the rollout. Once the post-split regression is
green and the deploy is healthy, the .bak file can be deleted.
"""

import logging

import azure.functions as func

from services.auth import bp as auth_bp
from services.autofill import bp as autofill_bp
from services.admin import bp as admin_bp
from services.billing import bp as billing_bp
from services.companies import bp as companies_bp
from services.harvest import bp as harvest_bp
from services.health import bp as health_bp
from services.jobs import bp as jobs_bp
from services.profile import bp as profile_bp
from services.resume import bp as resume_bp
from services.notifications import bp as notifications_bp

logger = logging.getLogger(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Order is informational only — Functions discovers all triggers regardless.
for _bp in (
    health_bp, auth_bp, profile_bp, resume_bp, companies_bp, jobs_bp,
    autofill_bp, harvest_bp, admin_bp, billing_bp, notifications_bp,
):
    app.register_functions(_bp)


# ── Backward-compatibility re-exports ───────────────────────────────────────
# A few tests and historical callers do `from function_app import <handler>`.
# Re-export the most-used handlers so those imports keep working post-split.
from services.health.routes import health_check  # noqa: E402,F401
from services.profile.routes import (  # noqa: E402,F401
    get_profile,
    update_profile,
    upload_resume,
)
from services._runtime import (  # noqa: E402,F401
    _extract_skills_from_resume,
    _extract_multipart_file,
    _ai_rerank_top_jobs,
)
