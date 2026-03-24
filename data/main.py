import os

from xai import Grok

API_KEY = os.environ.get("XAI_API_KEY")
if not API_KEY:
    raise ValueError("Set XAI_API_KEY in your environment before running this script.")

API_KEY = 'xai-PKmRa4frqly5uaztFQIz4q0jj4bzGcHECiN2yISN7QVkAhHrNwxJlVqIoUEdQNE3liWkilQLG9R2jiIk'

client = Grok(api_key=API_KEY)
response = client.chat.completions.create(
    model="grok-3",
    messages=[{"role": "user", "content": "Reason about this..."}]
)

