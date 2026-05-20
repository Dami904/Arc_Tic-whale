# social.py
from google import genai
from config import GOOGLE_API_KEY

client = genai.Client(api_key=GOOGLE_API_KEY)

# 🛠️ Set to True for infinite testing without hitting rate limits.
DEV_MODE = True

def generate_canteen_post(agent_name, action, tx_id):
    print(f"✍️ Drafting social post for @thecanteenapp...")
    
    short_tx = f"{tx_id[:6]}...{tx_id[-4:]}" if tx_id else "Pending"
    
    # --- 🛠️ DEV MODE BYPASS ---
    if DEV_MODE:
        print("🛠️ [DEV MODE ACTIVE]: Bypassing Gemini API for social post...")
        return f"\n📱 [POSTING TO @thecanteenapp]:\n(MOCK POST) The {agent_name} just executed a massive {action}. Accumulating while the market sleeps. 🐋📈\n🔗 Tx: {short_tx}\n"
    # --------------------------
    
    prompt = f"""
    You are the social media manager for an elite AI crypto trading agent named '{agent_name}'.
    The agent just successfully executed a {action} order on-chain.
    Write a sharp, engaging social media post (max 200 characters) announcing this move.
    Adopt a confident, analytical, "whale" persona. 
    Do not use hashtags. Do not use markdown.
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        post_content = response.text.strip()
        
        social_output = f"\n📱 [POSTING TO @thecanteenapp]:\n{post_content}\n🔗 Tx: {short_tx}\n"
        return social_output
        
    except Exception as e:
        print(f"⚠️ Debug GenAI Error: {e}")
        return f"📱 [AUTO-POST FAILED]: Executed {action}. Tx: {tx_id}"