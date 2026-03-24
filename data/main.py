import os

from xai import Grok

API_KEY = os.environ.get("XAI_API_KEY")
if not API_KEY:
    raise ValueError("Set XAI_API_KEY in your environment before running this script.")

client = Grok(api_key=API_KEY)
response = client.chat.completions.create(
    model="grok-3",
    messages=[{"role": "user", "content": "Reason about this..."}]
)

