"""Auth middleware — Google sign-in only.

login_with_google() verifies a Google ID token and issues an HS256 JWT signed
with JWT_SECRET. get_user_id() extracts the userId from that JWT on every
request. The legacy email/password endpoints have been removed.
"""

import os
import uuid
import logging
from datetime import datetime, timezone, timedelta

from jose import jwt, JWTError

from shared.exceptions import AuthenticationError, ValidationError
from shared.cosmos_client import create_item, query_items, upsert_item

logger = logging.getLogger(__name__)

# JWT signing key. Fail closed in production: never silently fall back to a
# well-known dev secret.
_DEV_SECRET = "autoapply-dev-secret-change-in-prod"
JWT_SECRET = os.environ.get("JWT_SECRET", _DEV_SECRET)
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72

# Allow the X-Dev-User-Id impersonation header only when explicitly enabled
# (local development). NEVER set this on the production Function App.
ALLOW_DEV_USER_HEADER = os.environ.get("ALLOW_DEV_USER_HEADER", "").lower() in ("1", "true", "yes")

# Google OAuth: client ID(s) accepted as the JWT 'aud' claim. Multiple IDs
# (e.g. web + extension) can be supplied comma-separated.
GOOGLE_CLIENT_IDS = [
    s.strip() for s in os.environ.get("GOOGLE_CLIENT_IDS", "").split(",") if s.strip()
]
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()


def _create_jwt(user_id: str, email: str) -> str:
    if JWT_SECRET == _DEV_SECRET:
        # Refuse to mint tokens with the well-known dev key.
        raise AuthenticationError(
            "Server misconfigured: JWT_SECRET must be set to a strong random value."
        )
    payload = {
        "sub": user_id,
        "email": email,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def login_with_google(id_token_str: str) -> dict:
    """Verify a Google ID token and sign the user in (creating the account on
    first login). Returns { userId, token, email, name }.
    """
    if not id_token_str:
        raise ValidationError("idToken is required")
    if not GOOGLE_CLIENT_IDS:
        raise ValidationError("Google sign-in is not configured on the server")

    # Lazy import so the rest of the API still loads if the dep isn't installed.
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    last_err = None
    info = None
    transport = google_requests.Request()
    for client_id in GOOGLE_CLIENT_IDS:
        try:
            info = google_id_token.verify_oauth2_token(id_token_str, transport, client_id)
            break
        except ValueError as e:
            last_err = e
    if info is None:
        raise AuthenticationError(f"Invalid Google ID token: {last_err}")

    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise AuthenticationError("Untrusted Google issuer")
    if not info.get("email_verified"):
        raise AuthenticationError("Google email is not verified")

    email = (info.get("email") or "").strip().lower()
    if not email:
        raise AuthenticationError("Google ID token missing email")
    name = (info.get("name") or info.get("given_name") or email.split("@")[0]).strip()
    google_sub = info.get("sub", "")

    now = datetime.now(timezone.utc).isoformat()

    # Find existing user by email; create on first login.
    users = query_items("users",
        "SELECT * FROM c WHERE c.email = @email",
        [{"name": "@email", "value": email}])
    if users:
        user = users[0]
        # Backfill googleSub on first Google login from a legacy account.
        if not user.get("googleSub"):
            user["googleSub"] = google_sub
            user["lastLoginAt"] = now
            upsert_item("users", user)
        user_id = user["id"]
        display_name = user.get("name", name)
    else:
        user_id = f"user-{uuid.uuid4().hex[:12]}"
        user = {
            "id": user_id,
            "email": email,
            "name": name,
            "googleSub": google_sub,
            "createdAt": now,
            "lastLoginAt": now,
        }
        create_item("users", user)
        # Create empty profile.
        parts = name.split() if name else [""]
        first = parts[0]
        rest = " ".join(parts[1:])
        profile = {
            "id": user_id,
            "userId": user_id,
            "type": "profile",
            "personal": {"firstName": first, "lastName": rest},
            "email": email,
            "skills": {"technical": [], "soft": []},
            "experience": [],
            "education": [],
            "preferences": {"roles": [], "locations": [], "salary": {}},
            "documents": {"resumeUrl": None, "resumeVersion": 0, "parsedResumeData": None},
            "linkedinUrl": "",
            "linkedinData": None,
            "selectedCompanies": [],
            "createdAt": now,
            "updatedAt": now,
        }
        create_item("profiles", profile)
        display_name = name

        # Welcome email (best-effort — never block login).
        try:
            from shared.email_service import send_welcome
            send_welcome(user_id=user_id, email=email, name=name)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("welcome email failed: %s", e)

    token = _create_jwt(user_id, email)
    return {"userId": user_id, "token": token, "email": email, "name": display_name}


def exchange_google_auth_code(code: str, redirect_uri: str, code_verifier: str) -> dict:
    """Exchange an OAuth authorization code (PKCE) for a Google ID token, then sign in."""
    import requests

    code = (code or "").strip()
    redirect_uri = (redirect_uri or "").strip()
    code_verifier = (code_verifier or "").strip()
    if not code or not redirect_uri or not code_verifier:
        raise ValidationError("code, redirectUri, and codeVerifier are required")
    if not GOOGLE_CLIENT_IDS:
        raise ValidationError("Google sign-in is not configured on the server")

    if not GOOGLE_CLIENT_SECRET:
        raise AuthenticationError(
            "Google sign-in is not fully configured on the server. "
            "Add GOOGLE_CLIENT_SECRET in the Function App settings "
            "(Google Cloud Console → Credentials → your Web client → Client secret)."
        )

    client_id = GOOGLE_CLIENT_IDS[0]
    token_data = {
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    token_data["client_secret"] = GOOGLE_CLIENT_SECRET
    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data=token_data,
            timeout=15,
        )
    except Exception as e:
        logger.warning("Google token exchange request failed: %s", e)
        raise AuthenticationError("Google sign-in failed. Try Safari or Chrome.") from e

    if resp.status_code != 200:
        err_body = {}
        try:
            err_body = resp.json() if resp.text else {}
        except Exception:
            pass
        err_desc = (err_body.get("error_description") or err_body.get("error") or "").strip()
        logger.warning(
            "Google token exchange HTTP %s: %s",
            resp.status_code,
            (resp.text or "")[:300],
        )
        if "client_secret is missing" in err_desc:
            raise AuthenticationError(
                "Google sign-in is not configured on the server (missing client secret)."
            )
        if err_desc:
            raise AuthenticationError(f"Google sign-in failed: {err_desc}")
        raise AuthenticationError(
            "Google sign-in failed. Open autoapplynow.in in Safari or Chrome and retry."
        )

    tokens = resp.json() if resp.text else {}
    id_token = tokens.get("id_token")
    if not id_token:
        raise AuthenticationError("Google did not return an ID token")
    return login_with_google(id_token_str=id_token)


def get_user_id(req) -> str:
    """Extract userId from a Bearer JWT.

    The X-Dev-User-Id impersonation header is only honored when
    ALLOW_DEV_USER_HEADER=true (local dev). It is rejected in production.
    """
    if ALLOW_DEV_USER_HEADER:
        dev_user = req.headers.get("X-Dev-User-Id")
        if dev_user:
            return dev_user

    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid Authorization header")

    token = auth_header[7:]
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        raise AuthenticationError(f"Invalid token: {e}")

    user_id = claims.get("sub")
    if not user_id:
        raise AuthenticationError("Token missing user identifier")

    return user_id


def get_user_claims(req) -> dict:
    """Same auth gate as get_user_id but returns the full JWT claims dict
    so callers can inspect email/tier without a second Cosmos read.
    """
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid Authorization header")
    token = auth_header[7:]
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        raise AuthenticationError(f"Invalid token: {e}")
    if not claims.get("sub"):
        raise AuthenticationError("Token missing user identifier")
    return claims
