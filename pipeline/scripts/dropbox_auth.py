"""
Run this once to obtain a refresh token. Copy the printed values into .env.

Steps:
  1. Go to https://www.dropbox.com/developers/apps
  2. Find your app → Settings tab → copy App key and App secret
  3. Put them in .env as DROPBOX_APP_KEY and DROPBOX_APP_SECRET
  4. Run: python scripts/dropbox_auth.py
  5. Follow the printed URL, authorise, paste the code back
  6. Copy the printed DROPBOX_REFRESH_TOKEN into .env
"""

import os
import dropbox
from dropbox import DropboxOAuth2FlowNoRedirect
from dotenv import load_dotenv

load_dotenv()

app_key = os.environ["DROPBOX_APP_KEY"]
app_secret = os.environ["DROPBOX_APP_SECRET"]

auth_flow = DropboxOAuth2FlowNoRedirect(
    app_key,
    app_secret,
    token_access_type="offline",
)

authorize_url = auth_flow.start()
print("1. Go to:", authorize_url)
print("2. Click 'Allow' (you may need to log in first)")
print("3. Copy the authorisation code")
auth_code = input("Enter the authorisation code here: ").strip()

oauth_result = auth_flow.finish(auth_code)

print()
print("Add this to your .env file:")
print(f"DROPBOX_REFRESH_TOKEN={oauth_result.refresh_token}")
