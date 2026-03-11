#!/usr/bin/env python3
"""
saudi-brief.py — Morning Saudi Arabia news briefing
Fetches Arabic/English news, filters for Jeddah/KAUST/Red Sea,
translates via LLM, sends to Discord.
Runs daily at 6am local time via cron.
"""

import urllib.request, urllib.parse, json, re, sys
from xml.etree import ElementTree as ET
from datetime import datetime, timezone

# Secrets — loaded from environment or openclaw config on VPS
import os
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_GENERAL_CHANNEL_ID", "1477761850748834008")
MODEL = "qwen/qwen3.5-flash-02-23"

# Fallback: load from openclaw.json if env vars not set
if not OPENROUTER_KEY or not DISCORD_TOKEN:
    try:
        with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
            cfg = json.load(f)
        if not OPENROUTER_KEY:
            OPENROUTER_KEY = cfg.get("env", {}).get("OPENROUTER_API_KEY", "")
        if not DISCORD_TOKEN:
            DISCORD_TOKEN = cfg.get("channels", {}).get("discord", {}).get("token", "")
    except Exception:
        pass

RSS_FEEDS = [
    # English - Google News targeted searches
    ("https://news.google.com/rss/search?q=Jeddah+OR+KAUST+Saudi+Arabia&hl=en-US&gl=US&ceid=US:en", "en"),
    ("https://news.google.com/rss/search?q=Red+Sea+Houthi+Saudi+Arabia&hl=en-US&gl=US&ceid=US:en", "en"),
    ("https://news.google.com/rss/search?q=Saudi+Arabia+security+military+attack&hl=en-US&gl=US&ceid=US:en", "en"),
    # Arabic - Okaz (Jeddah-based newspaper)
    ("https://www.okaz.com.sa/rssFeed/", "ar"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}


def fetch_rss(url, lang):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read().decode("utf-8", errors="replace")
        titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]>', raw)
        if not titles:
            root = ET.fromstring(raw)
            titles = [t.text or "" for t in root.findall(".//item/title")]
        descs = re.findall(r'<description><!\[CDATA\[(.*?)\]\]>', raw)
        if not descs:
            descs = [""] * len(titles)
        items = []
        for i, title in enumerate(titles[:15]):
            desc = descs[i] if i < len(descs) else ""
            desc = re.sub(r'<[^>]+>', '', desc)[:200]
            items.append({"title": title.strip(), "desc": desc.strip(), "lang": lang})
        return items
    except Exception:
        return []


def call_llm(headlines_text):
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": f"""You are briefing Eric Bakken, who has close ties to Jeddah and KAUST (King Abdullah University of Science and Technology) on the Red Sea coast of Saudi Arabia.

From the following news headlines (mix of English and Arabic), create a compact morning brief focused on:
1. Safety & security — any attacks, incidents, threats near Jeddah, Red Sea, or western Saudi Arabia
2. War/conflict sentiment — Houthi activity, Red Sea shipping, regional military tensions
3. KAUST or Jeddah-specific events
4. Any other significant Saudi news worth knowing

Rules:
- Translate any Arabic headlines to English
- Skip irrelevant items (sports, celebrity, unrelated regions)
- Maximum 10 bullet points, each <= 2 lines
- Start with most important/safety-relevant items
- If nothing significant: say "No major incidents in your area"
- End with one-line risk level: 🟢 Normal / 🟡 Elevated / 🔴 High

Headlines:
{headlines_text}"""
        }],
        "max_tokens": 600
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


def send_discord(msg):
    if len(msg) > 1990:
        msg = msg[:1990] + "..."
    payload = json.dumps({"content": msg}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
        data=payload,
        headers={
            "Authorization": f"Bot {DISCORD_TOKEN}",
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


# --- Main ---
all_headlines = []
for url, lang in RSS_FEEDS:
    items = fetch_rss(url, lang)
    all_headlines.extend(items)

if not all_headlines:
    send_discord("⚠️ Saudi brief: could not fetch any news feeds")
    sys.exit(1)

# Deduplicate by title similarity
seen = set()
unique = []
for item in all_headlines:
    key = item["title"][:40].lower()
    if key not in seen:
        seen.add(key)
        unique.append(item)

headlines_text = "\n".join(
    f"[{i['lang'].upper()}] {i['title']}" + (f" — {i['desc']}" if i['desc'] else "")
    for i in unique
)

brief = call_llm(headlines_text)
now = datetime.now(timezone.utc).strftime("%b %d, %H:%M UTC")
message = f"🇸🇦 **Saudi Brief — {now}**\n\n{brief}"
send_discord(message)
print("Sent.")
