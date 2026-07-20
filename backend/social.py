from google import genai
from backend.config import GOOGLE_API_KEY, SOCIAL_DEV_MODE
from backend.logger import get_logger

log = get_logger("social")

DEV_MODE = SOCIAL_DEV_MODE

def generate_canteen_post(agent_name, action, tx_id, reason=""):
    log.info("Drafting social post for @thecanteenapp...")

    short_tx = f"{tx_id[:6]}...{tx_id[-4:]}" if tx_id else "Pending"

    if DEV_MODE:
        log.info("DEV MODE ACTIVE: Bypassing Gemini API for social post")
        return f"\n📱 [POSTING TO @thecanteenapp]:\n(MOCK POST) The {agent_name} just executed a massive {action}. Accumulating while the market sleeps. 🐋📈\n🔗 Tx: {short_tx}\n"

    if not GOOGLE_API_KEY:
        return f"📱 [AUTO-POST SKIPPED]: Executed {action}. Tx: {tx_id}"

    prompt = f"""
    You are the social media manager for an elite AI crypto trading agent named '{agent_name}'.
    The agent just successfully executed a {action} order on-chain.
    Trading context: {reason or 'No extra context supplied.'}
    Write a sharp, engaging social media post (max 200 characters) announcing this move.
    Adopt a confident, analytical, "whale" persona. 
    Do not use hashtags. Do not use markdown.
    """

    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite',
            contents=prompt
        )
        post_content = response.text.strip()

        social_output = f"\n📱 [POSTING TO @thecanteenapp]:\n{post_content}\n🔗 Tx: {short_tx}\n"
        return social_output

    except Exception as e:
        log.warning("GenAI social post failed", error=str(e))
        return f"📱 [AUTO-POST FAILED]: Executed {action}. Tx: {tx_id}"
