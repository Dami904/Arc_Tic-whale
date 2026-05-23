"""Optional Resend delivery for dev/custom OTP fallback."""

from __future__ import annotations

import httpx

from backend.config import RESEND_API_KEY, RESEND_FROM_EMAIL
from backend.logger import get_logger

log = get_logger("email_otp")


async def send_otp_email(to_email: str, code: str) -> bool:
    """Send OTP via Resend. Returns True if sent, False if not configured or failed."""
    if not RESEND_API_KEY:
        return False

    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": "Your Arc_Tic Whale sign-in code",
        "html": (
            f"<p>Your verification code is:</p>"
            f"<p style='font-size:28px;font-weight:bold;letter-spacing:4px'>{code}</p>"
            f"<p>This code expires in 10 minutes.</p>"
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
        if r.status_code in (200, 201):
            return True
        log.warning("Resend returned %s: %s", r.status_code, r.text[:300])
    except Exception as exc:
        log.warning("Resend send failed: %s", exc)
    return False
