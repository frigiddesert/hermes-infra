# Human Tasks — Week of Feb 23, 2026
### Backblaze B2 Backups + OneDrive via rclone

---

## Task 1 — Backblaze B2 (5 min)

Log in to backblaze.com, create a bucket and an application key.

Give Claude:
```
B2_BUCKET=
B2_KEY_ID=
B2_APP_KEY=
```

Claude will handle the rest (install rclone, configure remote, backup script, cron).

---

## Task 2 — OneDrive via Azure App (15 min)

Go to **portal.azure.com** and do the following:

### Step 1 — Create App Registration
- **Azure Active Directory → App registrations → New registration**
- Name: `rclone-openclaw`
- Supported account types: *Accounts in this organizational directory only* (business OneDrive)
  - Or: *Personal Microsoft accounts only* if it's a personal OneDrive
- Redirect URI: **Web** → `http://localhost:53682/`
- Click **Register**

### Step 2 — Copy these (give to Claude)
On the app overview page:
- **Application (client) ID**
- **Directory (tenant) ID**

### Step 3 — Create a Client Secret
- Left menu → **Certificates & secrets → New client secret**
- Description: `rclone`, Expiry: 24 months
- Click **Add**
- ⚠️ Copy the **Value** immediately — it disappears after you leave the page

### Step 4 — Add API Permissions
- Left menu → **API permissions → Add a permission → Microsoft Graph → Delegated**
- Add all of these:
  - `Files.ReadWrite`
  - `Files.ReadWrite.All`
  - `offline_access`
  - `User.Read`
- Click **Grant admin consent** (blue button at top of permissions list)

### Give Claude:
```
ONEDRIVE_CLIENT_ID=
ONEDRIVE_TENANT_ID=
ONEDRIVE_CLIENT_SECRET=
```

---

## What Claude Will Do After You Provide Keys

1. Install rclone on VPS
2. Configure Backblaze B2 remote
3. Configure OneDrive remote
4. Run a one-time OAuth flow — you'll click a link on your local machine, paste a code back
5. Create `openclaw/` folder structure on OneDrive
6. Write backup script (Postgres dump + workspace + config + scripts)
7. Add backup cron (daily at 3 AM Jeddah / midnight UTC)
8. Test both remotes and confirm first backup ran

---

## Status
- [ ] Backblaze keys provided
- [ ] Azure app created + credentials provided
- [ ] OAuth flow completed (OneDrive)
- [ ] First backup confirmed
