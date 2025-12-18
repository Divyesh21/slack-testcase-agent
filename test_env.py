
import os
import requests
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("SLACK_BOT_TOKEN")

response = requests.get(
    "https://slack.com/api/auth.test",
    headers={"Authorization": f"Bearer {token}"}
)

print("[Slack Token Check]", response.json())
