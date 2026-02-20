"""
scripts/oauth_setup.py — One-time OAuth flow for Google and Microsoft.

Saves refresh tokens to ~/.openclaw/credentials/<provider>_token.json
so the bot can use them without re-authenticating.

Usage:
    python scripts/oauth_setup.py --provider google
    python scripts/oauth_setup.py --provider microsoft
"""

import argparse
import json
import os
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import requests
from dotenv import load_dotenv

load_dotenv()

CREDENTIALS_DIR = Path.home() / ".openclaw" / "credentials"
CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)

CALLBACK_PORT = 8765


# ── Google ────────────────────────────────────────────────────────────────────

GOOGLE_SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
])


def google_auth():
    client_id = os.environ["GOOGLE_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_CLIENT_SECRET"]
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", f"http://localhost:{CALLBACK_PORT}/oauth/google/callback")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

    code = _browser_auth_flow(auth_url, redirect_uri, "google")

    # Exchange code for tokens
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    token_data = resp.json()

    # Get user email
    info = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    ).json()

    token_data["email"] = info.get("email", "")
    token_data["client_id"] = client_id
    token_data["client_secret"] = client_secret
    token_data["redirect_uri"] = redirect_uri

    out = CREDENTIALS_DIR / "google_token.json"
    out.write_text(json.dumps(token_data, indent=2))
    print(f"\n✅ Google token saved → {out}")
    print(f"   Account: {token_data['email']}")
    if "refresh_token" not in token_data:
        print("   ⚠️  No refresh_token — if you see this, revoke access at")
        print("   https://myaccount.google.com/permissions and re-run")


# ── Microsoft ─────────────────────────────────────────────────────────────────

MICROSOFT_SCOPES = " ".join([
    "Mail.Read",
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.Read",
    "Calendars.ReadWrite",
    "offline_access",
    "User.Read",
])


def microsoft_auth():
    client_id = os.environ["MICROSOFT_CLIENT_ID"]
    client_secret = os.environ["MICROSOFT_CLIENT_SECRET"]
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "common")
    redirect_uri = os.getenv("MICROSOFT_REDIRECT_URI", f"http://localhost:{CALLBACK_PORT}/oauth/microsoft/callback")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": MICROSOFT_SCOPES,
        "response_mode": "query",
    }
    auth_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)

    code = _browser_auth_flow(auth_url, redirect_uri, "microsoft")

    # Exchange code for tokens
    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    token_data = resp.json()

    # Get user email
    info = requests.get(
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    ).json()

    token_data["email"] = info.get("mail") or info.get("userPrincipalName", "")
    token_data["client_id"] = client_id
    token_data["client_secret"] = client_secret
    token_data["tenant_id"] = tenant_id
    token_data["redirect_uri"] = redirect_uri

    out = CREDENTIALS_DIR / "microsoft_token.json"
    out.write_text(json.dumps(token_data, indent=2))
    print(f"\n✅ Microsoft token saved → {out}")
    print(f"   Account: {token_data['email']}")


# ── Local callback server ─────────────────────────────────────────────────────

_received_code: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _received_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _received_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Auth complete. You can close this tab.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h2>No code received.</h2>")

    def log_message(self, *args):
        pass  # suppress request logs


def _browser_auth_flow(auth_url: str, redirect_uri: str, provider: str) -> str:
    global _received_code
    _received_code = None

    # Extract path from redirect_uri for the server to match
    path = urllib.parse.urlparse(redirect_uri).path

    server = HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"\n[{provider}] Opening browser for authorization...")
    print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    thread.join(timeout=120)
    server.server_close()

    if not _received_code:
        print("\nBrowser flow timed out or no code received.")
        code = input("Paste the authorization code manually: ").strip()
        return code

    return _received_code


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OAuth setup for openclaw integrations")
    parser.add_argument("--provider", required=True, choices=["google", "microsoft"])
    args = parser.parse_args()

    if args.provider == "google":
        required = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            print(f"Missing env vars: {', '.join(missing)}")
            print("See docs/OAUTH_SETUP_GUIDE.md")
            sys.exit(1)
        google_auth()

    elif args.provider == "microsoft":
        required = ["MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            print(f"Missing env vars: {', '.join(missing)}")
            print("See docs/OAUTH_SETUP_GUIDE.md")
            sys.exit(1)
        microsoft_auth()


if __name__ == "__main__":
    main()
