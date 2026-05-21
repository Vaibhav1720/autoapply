"""IP → country-code geolocation helper.

Uses ipinfo.io (50 k req/month free, HTTPS, no API key needed for basic lookups).
Results are cached in-process for 1 hour to stay well within rate limits.

Usage
-----
    from shared.geoip import country_for_request, country_for_ip

    # Preferred — reads X-Forwarded-For from the incoming Azure Functions request.
    code = country_for_request(req)   # returns "IN", "US", "" on failure

    # Direct lookup (useful in tests).
    code = country_for_ip("203.0.113.1")

Design notes
------------
* The IP is taken from the RIGHTMOST entry in X-Forwarded-For so that Azure's
  load-balancer-appended value is used, not a client-supplied header.
* Private / loopback addresses are skipped and return "" (dev / local traffic).
* On lookup failure (timeout, API error, rate-limit) the function returns ""
  so callers can fall back gracefully.
* The in-process LRU-style dict survives the lifetime of one warm Function App
  instance — cold starts get a fresh cache, which is acceptable.
"""

from __future__ import annotations

import logging
import time
import urllib.request
import urllib.error

import azure.functions as func

logger = logging.getLogger(__name__)

_TIMEOUT_S = 3           # max latency budget per lookup
_CACHE_TTL_S = 3600      # 1 hour
_CACHE: dict[str, tuple[str, float]] = {}   # ip → (country_code, epoch_ts)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def country_for_request(req: func.HttpRequest) -> str:
    """Extract client IP from the request and return its ISO country code.

    Reads X-Forwarded-For (rightmost = most trusted, set by Azure LB).
    Falls back to X-Real-IP, then CF-Connecting-IP if present.
    Returns '' if the IP is private/loopback or on any lookup failure.
    """
    ip = _extract_ip(req)
    if not ip:
        return ""
    return country_for_ip(ip)


def country_for_ip(ip: str) -> str:
    """Return ISO 3166-1 alpha-2 country code for *ip*, or '' on failure."""
    ip = ip.strip()
    if not ip or _is_private(ip):
        return ""

    cached = _CACHE.get(ip)
    if cached and (time.monotonic() - cached[1]) < _CACHE_TTL_S:
        return cached[0]

    code = _lookup(ip)
    if code:
        _CACHE[ip] = (code, time.monotonic())
    return code


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_ip(req: func.HttpRequest) -> str:
    """Return the most-trusted client IP from the request headers."""
    # X-Forwarded-For may be a comma-separated list: client, proxy1, proxy2, ...
    # Azure's load balancer APPENDS its view of the remote address, so the
    # rightmost entry is the one Azure verified — clients cannot spoof it.
    xff = req.headers.get("X-Forwarded-For", "").strip()
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            # Use rightmost (Azure-appended) value, strip port if IPv4:port.
            return _strip_port(parts[-1])

    for header in ("X-Real-IP", "CF-Connecting-IP", "True-Client-IP"):
        val = req.headers.get(header, "").strip()
        if val:
            return _strip_port(val)

    return ""


def _strip_port(addr: str) -> str:
    """Remove :port from an IPv4 address string, leave IPv6 alone."""
    if addr.count(":") == 1:          # IPv4:port
        return addr.split(":")[0]
    return addr


def _lookup(ip: str) -> str:
    """Call ipinfo.io and return the 2-letter country code, or '' on error."""
    try:
        url = f"https://ipinfo.io/{ip}/country"
        req = urllib.request.Request(
            url, headers={"User-Agent": "AutoApply-GeoIP/1.0", "Accept": "text/plain"}
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip().upper()
            # ipinfo returns bare "IN\n" — validate it's a 2-letter code.
            if len(body) == 2 and body.isalpha():
                logger.debug("[GEOIP] %s → %s", ip, body)
                return body
            logger.warning("[GEOIP] unexpected response for %s: %r", ip, body[:20])
    except urllib.error.HTTPError as e:
        logger.warning("[GEOIP] HTTP %s for ip=%s", e.code, ip)
    except Exception as e:
        logger.warning("[GEOIP] lookup failed for %s: %s", ip, e)
    return ""


# Private / RFC-1918 / loopback / link-local ranges.
_PRIVATE_PREFIXES = (
    "127.", "10.", "192.168.",
    "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
    "169.254.",   # link-local
    "::1", "fc", "fd",  # IPv6 loopback + ULA
)


def _is_private(ip: str) -> bool:
    return any(ip.startswith(p) for p in _PRIVATE_PREFIXES)
