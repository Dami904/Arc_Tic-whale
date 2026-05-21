import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import base64
import httpx
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA256

from backend.config import CIRCLE_API_KEY

def generate_entity_secret():
    # 1. Generate the raw 32-byte secret (This is your ultimate master key)
    raw_secret = os.urandom(32).hex()
    
    print("\n" + "="*50)
    print("🚨 YOUR RAW ENTITY SECRET (SAVE THIS SECURELY!) 🚨")
    print("="*50)
    print(f"{raw_secret}\n")
    print("Add this to your .env file. If you lose this, you lose the wallet forever.\n")

    # 2. Fetch Circle's Public Key
    print("Fetching Circle's Public Key...")
    url = "https://api.circle.com/v1/w3s/config/entity/publicKey"
    headers = {"Authorization": f"Bearer {CIRCLE_API_KEY}"}
    response = httpx.get(url, headers=headers)
    
    if response.status_code != 200:
        print(f"❌ Error fetching key: {response.text}")
        return
        
    public_key_pem = response.json()['data']['publicKey']

    # 3. Encrypt the secret using RSA-OAEP with SHA-256
    recipient_key = RSA.import_key(public_key_pem)
    cipher_rsa = PKCS1_OAEP.new(recipient_key, hashAlgo=SHA256)
    encrypted_secret = cipher_rsa.encrypt(bytes.fromhex(raw_secret))
    
    # 4. Encode to Base64 to get the final Ciphertext
    ciphertext = base64.b64encode(encrypted_secret).decode('utf-8')

    print("="*50)
    print("✅ PASTE THIS CIPHERTEXT INTO THE BROWSER BOX ✅")
    print("="*50)
    print(ciphertext)
    print("\n")

if __name__ == "__main__":
    generate_entity_secret()
