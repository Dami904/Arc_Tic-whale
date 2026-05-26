import re

from backend.config import AGENT_WALLET_ADDRESS, TRADE_DRY_RUN
from backend.database import get_user, get_user_by_referral_code, upsert_user_wallet, update_user_profile
from backend.wallet_manager import create_agent_wallet, create_wallet_with_policy
from backend.wallet_funding import fund_new_user_wallet
from backend.logger import get_logger

log = get_logger("user_wallets")


def normalize_user_id(username: str) -> str:
    return re.sub(r"[^A-Za-z0-9_:-]", "_", str(username or "").strip().lstrip("@"))[:64]


def referral_code_for_user(user_id: str) -> str:
    safe = normalize_user_id(user_id).lower()
    return f"ref_{safe}"


def resolve_referrer(referral_code: str | None, user_id: str) -> str | None:
    if not referral_code:
        return None

    code = referral_code.strip().lstrip("/")
    if not code:
        return None

    referrer = get_user_by_referral_code(code)
    if not referrer and not code.startswith("ref_"):
        referrer = get_user_by_referral_code(referral_code_for_user(code))

    if not referrer or referrer["user_id"] == user_id:
        return None
    return referrer["user_id"]


def ensure_user_wallet(
    username: str,
    referral_code: str | None = None,
    email: str | None = None,
    display_name: str | None = None,
    avatar_url: str | None = None,
    telegram_chat_id: str | None = None,
) -> dict | None:
    user_id = normalize_user_id(username)
    if not user_id:
        return None

    existing = get_user(user_id)
    if existing:
        # Never overwrite a custom display_name with a generic login-time label.
        # Only set display_name when the existing record has none (first-time onboarding).
        existing_name = existing.get("display_name") or ""
        resolved_name = display_name if (display_name and not existing_name) else existing_name or None
        update_user_profile(
            user_id,
            email=email if email is not None else existing.get("email"),
            display_name=resolved_name,
            avatar_url=avatar_url if avatar_url is not None else existing.get("avatar_url"),
            telegram_chat_id=telegram_chat_id if telegram_chat_id is not None else existing.get("telegram_chat_id"),
        )
        return get_user(user_id)

    if TRADE_DRY_RUN:
        wallet_id = f"dryrun-user-{user_id}"
        wallet_address = AGENT_WALLET_ADDRESS or "0x0000000000000000000000000000000000000000"
    else:
        log.info("Provisioning first-access Arc Testnet wallet for @%s...", user_id)
        wallet_record = create_wallet_with_policy(f"User_{user_id}", daily_limit=50.0, max_per_tx=2.0)
        if not wallet_record:
            return None
        wallet_id = wallet_record.get("wallet_id")
        wallet_address = wallet_record.get("address")

    if not wallet_id or not wallet_address:
        return None

    user = upsert_user_wallet(
        user_id=user_id,
        wallet_id=wallet_id,
        wallet_address=wallet_address,
        referral_code=referral_code_for_user(user_id),
        referred_by=resolve_referrer(referral_code, user_id),
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
        telegram_chat_id=telegram_chat_id,
    )
    fund_new_user_wallet(user_id=user_id, destination_address=wallet_address)
    return user
