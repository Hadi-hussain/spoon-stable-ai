import os
from dotenv import load_dotenv
from groq import Groq

# Force load fresh .env values
load_dotenv(override=True)

key = os.getenv("GROQ_API_KEY", "").strip()
print(f"Testing Key (First 10 chars): {key[:10]}...")

try:
    client = Groq(api_key=key)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=10
    )
    print("✅ SUCCESS! Groq API Response:", response.choices[0].message.content)
except Exception as e:
    print("❌ GROQ API ERROR:", e)
