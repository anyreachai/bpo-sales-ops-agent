"""
One-time Google OAuth2 consent flow to obtain a refresh token.

Usage:
    1. Create an OAuth 2.0 Client ID (Desktop app) in GCP Console:
       https://console.cloud.google.com/apis/credentials?project=anyreach-console
    2. Add GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET to .env
    3. Enable Gmail API and Google Calendar API in the project
    4. Run: python setup/google_oauth.py
    5. Browser opens → sign in with richard@anyreach.ai → grant access
    6. Refresh token is saved to .env automatically
"""
import http.server
import json
import os
import sys
import threading
import urllib.parse
import webbrowser

import httpx

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

REDIRECT_URI = "http://localhost:8888"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"


def load_env() -> dict:
    env = {}
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env[key.strip()] = val.strip()
    return env


def save_refresh_token(token: str) -> None:
    env_path = os.path.join(BASE_DIR, ".env")
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.strip().startswith("GOOGLE_OAUTH_REFRESH_TOKEN="):
                    lines.append(f"GOOGLE_OAUTH_REFRESH_TOKEN={token}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"\n# Google OAuth (obtained via setup/google_oauth.py)\n")
        lines.append(f"GOOGLE_OAUTH_REFRESH_TOKEN={token}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)
    print(f"Saved refresh token to {env_path}")


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    resp = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Token exchange failed: {resp.status_code}")
        print(resp.text)
        sys.exit(1)
    return resp.json()


def main():
    env = load_env()
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", env.get("GOOGLE_OAUTH_CLIENT_ID", ""))
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", env.get("GOOGLE_OAUTH_CLIENT_SECRET", ""))

    if not client_id or not client_secret:
        print("ERROR: GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set in .env")
        print()
        print("Steps:")
        print("  1. Go to https://console.cloud.google.com/apis/credentials?project=anyreach-console")
        print("  2. Create OAuth 2.0 Client ID → Desktop app")
        print("  3. Copy the Client ID and Client Secret")
        print("  4. Add to .env:")
        print("     GOOGLE_OAUTH_CLIENT_ID=<client-id>")
        print("     GOOGLE_OAUTH_CLIENT_SECRET=<client-secret>")
        sys.exit(1)

    # Build authorization URL
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

    # Start local callback server
    auth_code = None
    server_error = None

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code, server_error
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)

            if "error" in qs:
                server_error = qs["error"][0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"OAuth error: {server_error}".encode())
            elif "code" in qs:
                auth_code = qs["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Authorization successful! You can close this tab.")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing authorization code")

        def log_message(self, format, *args):
            pass  # Suppress request logs

    server = http.server.HTTPServer(("localhost", 8888), CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print("Opening browser for Google OAuth consent...")
    print(f"If browser doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback (5 minutes)
    server_thread.join(timeout=300)
    server.server_close()

    if server_error:
        print(f"OAuth error: {server_error}")
        sys.exit(1)
    if not auth_code:
        print("Timed out waiting for OAuth callback (300s)")
        print("\nTroubleshooting:")
        print("  1. Make sure you completed the Google sign-in in the browser")
        print("  2. Check that http://localhost:8888 is not blocked by a firewall")
        print("  3. Verify redirect URI in GCP Console → Credentials → your OAuth client:")
        print("     Add http://localhost:8888/callback as an Authorized redirect URI")
        print("  4. If using 'Desktop app' type, try 'Web application' type instead")
        sys.exit(1)

    print("Exchanging authorization code for tokens...")
    tokens = exchange_code(client_id, client_secret, auth_code)

    if "refresh_token" not in tokens:
        print("ERROR: No refresh token returned. Try revoking access and running again:")
        print("  https://myaccount.google.com/permissions")
        sys.exit(1)

    save_refresh_token(tokens["refresh_token"])
    print(f"\nAccess token (expires in {tokens.get('expires_in', '?')}s): {tokens['access_token'][:20]}...")
    print("Refresh token saved to .env — provision.py will use it to generate fresh access tokens.")
    print("\nNext step: python setup/provision.py")


if __name__ == "__main__":
    main()
