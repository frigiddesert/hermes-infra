# OAuth Setup Guide — Gmail, Google Calendar, Office 365

Work through this when you're ready (takes ~30 minutes total).
After each section you'll have credentials to paste into `.env`.

---

## Part 1 — Google (Gmail + Google Calendar)

### Step 1 — Create a Google Cloud Project

1. Go to **https://console.cloud.google.com/**
2. Click the project dropdown (top left) → **New Project**
3. Name it something like `openclaw-personal`
4. Click **Create** and wait a few seconds

### Step 2 — Enable APIs

In your new project:

1. Go to **APIs & Services → Library**
2. Search for and enable each of these:
   - **Gmail API** → Enable
   - **Google Calendar API** → Enable
   - **People API** → Enable (for contact lookup, optional but useful)

### Step 3 — Configure OAuth Consent Screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** (unless you have Google Workspace, then choose Internal)
3. Fill in:
   - App name: `openclaw`
   - User support email: your Gmail
   - Developer contact: your Gmail
4. Click **Save and Continue**
5. On **Scopes** — click **Add or Remove Scopes** and add:
   ```
   https://www.googleapis.com/auth/gmail.readonly
   https://www.googleapis.com/auth/gmail.send
   https://www.googleapis.com/auth/gmail.modify
   https://www.googleapis.com/auth/gmail.compose
   https://www.googleapis.com/auth/calendar
   https://www.googleapis.com/auth/calendar.events
   ```
6. Click **Save and Continue**
7. On **Test Users** — add your own Gmail address
8. Click **Save and Continue** → **Back to Dashboard**

### Step 4 — Create OAuth Credentials

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Name: `openclaw-desktop`
5. Click **Create**
6. A dialog shows your **Client ID** and **Client Secret** — copy both
7. Also click **Download JSON** and save as `google_oauth_client.json` somewhere safe

### Step 5 — Collect these values for `.env`

```env
GOOGLE_CLIENT_ID=<your-client-id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<your-client-secret>
GOOGLE_REDIRECT_URI=http://localhost:8765/oauth/google/callback
```

> **Note:** The redirect URI just needs to match what you configure — `localhost:8765` works
> fine for the one-time auth flow even on a server (you'll authorize locally and paste the token).

---

## Part 2 — Microsoft (Office 365 + Outlook + Microsoft Calendar)

### Step 1 — Register an Azure App

1. Go to **https://portal.azure.com/**
2. Search for **App registrations** in the top search bar
3. Click **+ New registration**
4. Fill in:
   - Name: `openclaw`
   - Supported account types: **Accounts in any organizational directory and personal Microsoft accounts**
   - Redirect URI: Select **Web** and enter `http://localhost:8765/oauth/microsoft/callback`
5. Click **Register**

### Step 2 — Note your App IDs

On the app overview page, copy:
- **Application (client) ID** → this is your `MICROSOFT_CLIENT_ID`
- **Directory (tenant) ID** → use `common` for personal + org accounts

### Step 3 — Create a Client Secret

1. In your app, go to **Certificates & secrets**
2. Click **+ New client secret**
3. Description: `openclaw-secret`
4. Expires: **24 months** (longest available)
5. Click **Add**
6. **Copy the Value immediately** — it's only shown once
   → this is your `MICROSOFT_CLIENT_SECRET`

### Step 4 — Set API Permissions

1. Go to **API permissions → + Add a permission**
2. Choose **Microsoft Graph**
3. Choose **Delegated permissions**
4. Add these permissions:
   ```
   Mail.Read
   Mail.ReadWrite
   Mail.Send
   Calendars.Read
   Calendars.ReadWrite
   offline_access
   User.Read
   ```
5. Click **Add permissions**
6. Click **Grant admin consent** (if you're an admin) — or skip for personal accounts

### Step 5 — Collect these values for `.env`

```env
MICROSOFT_CLIENT_ID=<your-application-client-id>
MICROSOFT_CLIENT_SECRET=<your-client-secret-value>
MICROSOFT_TENANT_ID=common
MICROSOFT_REDIRECT_URI=http://localhost:8765/oauth/microsoft/callback
```

---

## Part 3 — Run the Auth Flow (after you have credentials)

Once credentials are in `.env`, run the one-time auth flow to get refresh tokens:

```bash
cd /home/eric/code/claude-telegram-router
.venv/bin/python scripts/oauth_setup.py --provider google
# Opens a URL → authorize in browser → paste the code back
# Saves refresh token to ~/.openclaw/credentials/google_token.json

.venv/bin/python scripts/oauth_setup.py --provider microsoft
# Same flow for Microsoft
```

The refresh tokens are long-lived (Google: never expire if used regularly;
Microsoft: 90 days, auto-refreshed by the bot).

---

## Part 4 — openclaw Integration

After tokens are saved, add to openclaw config on the VPS:

```bash
ssh openclaw

# Gmail channel
openclaw channels add --channel gmail

# The gateway will detect token files in ~/.openclaw/credentials/
# and connect Gmail automatically on next restart
systemctl --user restart openclaw-gateway
```

---

## Part 5 — What the bot can do once connected

| Ask the bot... | It does... |
|----------------|-----------|
| "Any important emails today?" | Summarizes unread inbox |
| "Search my email for invoices from March" | Gmail search → summarized results |
| "Send Eric a reply to his last email saying I'll call Thursday" | Drafts + sends |
| "What's on my calendar this week?" | Lists events with times + locations |
| "Book a 1hr call with john@example.com next Tuesday at 2pm" | Creates calendar event + sends invite |
| "Clear my Thursday afternoon" | Deletes/declines conflicting events |

---

## Checklist

- [ ] Google Cloud project created
- [ ] Gmail API + Google Calendar API enabled
- [ ] OAuth consent screen configured
- [ ] Google client ID + secret copied → into `.env`
- [ ] Azure app registered
- [ ] Microsoft client ID + secret copied → into `.env`
- [ ] Microsoft API permissions added
- [ ] Run `oauth_setup.py --provider google` (after credentials in `.env`)
- [ ] Run `oauth_setup.py --provider microsoft`
- [ ] openclaw gateway restarted on VPS
- [ ] Test: ask the bot to read your inbox

---

## Troubleshooting

**"Access blocked: This app's request is invalid"**
→ Your redirect URI in the app registration doesn't match `GOOGLE_REDIRECT_URI` in `.env`

**"invalid_client" from Google**
→ Wrong client secret — re-copy from the Credentials page (not the download JSON)

**Microsoft "AADSTS50011: The redirect URI specified in the request does not match"**
→ Add `http://localhost:8765/oauth/microsoft/callback` in Azure → App → Authentication → Redirect URIs

**Token expires / revoked**
→ Re-run `oauth_setup.py` for that provider

---

*Next step after filling this in: run `oauth_setup.py` and we'll wire it into openclaw.*
