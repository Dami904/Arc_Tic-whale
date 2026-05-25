"""Verify Privy access tokens (ES256) via JWKS or optional PEM from dashboard."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import jwt as pyjwt
from jwt import PyJWKClient

from backend.config import PRIVY_APP_ID, PRIVY_VERIFICATION_KEY
from backend.logger import get_logger

log = get_logger("privy_jwt")

PRIVY_ISSUER = "privy.io"
JWKS_URL = f"https://auth.privy.io/v1/apps/{PRIVY_APP_ID}/jwks.json" if PRIVY_APP_ID else ""


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient | None:
    if not JWKS_URL:
        return None
    return PyJWKClient(JWKS_URL, cache_keys=True)


def _decode_with_pem(token: str) -> dict[str, Any]:
    if not PRIVY_VERIFICATION_KEY:
        raise ValueError("No verification key configured")
    pem = PRIVY_VERIFICATION_KEY  # newlines already normalised in config.py
    return pyjwt.decode(
        token,
        pem,
        algorithms=["ES256"],
        audience=PRIVY_APP_ID,
        issuer=PRIVY_ISSUER,
        options={"require": ["exp", "sub"]},
    )


def _decode_with_jwks(token: str) -> dict[str, Any]:
    client = _jwks_client()
    if client is None:
        raise ValueError("JWKS client not configured")
    signing_key = client.get_signing_key_from_jwt(token)
    return pyjwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        audience=PRIVY_APP_ID,
        issuer=PRIVY_ISSUER,
        options={"require": ["exp", "sub"]},
    )


def verify_privy_access_token(token: str) -> str:
    """
    Validate a Privy access token and return the user DID (sub claim).
    Raises jwt.InvalidTokenError on failure.
    """
    if not PRIVY_APP_ID:
        raise ValueError("PRIVY_APP_ID is not configured")

    last_error: Exception | None = None
    if PRIVY_VERIFICATION_KEY:
        try:
            payload = _decode_with_pem(token)
            return str(payload["sub"])
        except Exception as exc:
            last_error = exc
            log.debug("PEM JWT verify failed: %s", exc)

    try:
        payload = _decode_with_jwks(token)
        return str(payload["sub"])
    except Exception as exc:
        last_error = exc
        log.debug("JWKS JWT verify failed: %s", exc)

    if last_error:
        raise last_error
    raise ValueError("Unable to verify Privy token")

