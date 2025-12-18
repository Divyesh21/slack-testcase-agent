import os
import requests
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- JIRA VALIDATION ---

JIRA_DOMAIN = os.getenv("JIRA_DOMAIN")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
TICKET_ID = "DRG-114462"  # Replace with a valid Jira ticket in your account

jira_url = f"{JIRA_DOMAIN}/rest/api/3/issue/{TICKET_ID}"
jira_auth = (JIRA_EMAIL, JIRA_API_TOKEN)
jira_headers = { "Accept": "application/json" }

print("\n🔍 Verifying JIRA credentials...")
jira_response = requests.get(jira_url, headers=jira_headers, auth=jira_auth)

print(f"[JIRA] Status Code: {jira_response.status_code}")
print(f"[JIRA] Response: {jira_response.text}")

# --- OPENAI VALIDATION ---

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
print("\n🔍 Verifying OpenAI credentials...")

client = OpenAI(api_key=OPENAI_API_KEY)

try:
    models = client.models.list()
    print("[OpenAI] ✅ API key is valid. Models available:")
    for model in models.data[:3]:
        print(f" - {model.id}")
except Exception as e:
    print(f"[OpenAI] ❌ Error validating OpenAI key:\n{e}")

