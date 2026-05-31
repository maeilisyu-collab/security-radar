# © 2026 SecurityRadar (securityradar.io). All rights reserved.
# Unauthorized copying or distribution is prohibited.
"""
SecurityRadar — English News Auto-Fetch + AI Summary Script
============================================================
Usage:
  1. Local:          python fetch_news_en.py
  2. GitHub Actions (free automation):
     - Upload this file to your GitHub repository
     - Create .github/workflows/fetch_news_en.yml (see comment below)
     - Runs automatically every day at 10:00 AM UTC+9
     - GitHub Settings → Secrets → ANTHROPIC_API_KEY

Output: news_data_en.json (fetched by the HTML page)

[GitHub Actions Workflow]
--------------------------------------------------
# .github/workflows/fetch_news_en.yml
name: Security News Auto-Fetch (EN)

on:
  schedule:
    - cron: '0 1 * * *'   # Daily at 10:00 AM KST (UTC 01:00)
  workflow_dispatch:        # Manual trigger available

jobs:
  fetch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install requests feedparser anthropic
      - run: python fetch_news_en.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - name: Commit results
        run: |
          git config user.name "news-bot"
          git config user.email "bot@example.com"
          git add news_data_en.json
          git diff --staged --quiet || git commit -m "News update $(date '+%Y-%m-%d %H:%M')"
          git push
--------------------------------------------------
"""

import feedparser
import requests
import json
import os
import time
import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# ── RSS Sources ──────────────────────────────────────────────────────────────
RSS_SOURCES = [
    {
        "name": "The Hacker News",
        "url": "https://feeds.feedburner.com/TheHackersNews",
        "lang": "en",
        "priority": 1,
    },
    {
        "name": "BleepingComputer",
        "url": "https://www.bleepingcomputer.com/feed/",
        "lang": "en",
        "priority": 2,
    },
    {
        "name": "Krebs on Security",
        "url": "https://krebsonsecurity.com/feed/",
        "lang": "en",
        "priority": 3,
    },
    {
        "name": "CISA Alerts",
        "url": "https://www.cisa.gov/cybersecurity-advisories/all.xml",
        "lang": "en",
        "priority": 4,
    },
    {
        "name": "Google News — Cybersecurity",
        "url": (
            "https://news.google.com/rss/search"
            "?q=cybersecurity+hacking+ransomware&hl=en&gl=US&ceid=US:en"
        ),
        "lang": "en",
        "priority": 5,
    },
    {
        "name": "Google News — Vulnerability",
        "url": (
            "https://news.google.com/rss/search"
            "?q=vulnerability+CVE+zero-day+malware&hl=en&gl=US&ceid=US:en"
        ),
        "lang": "en",
        "priority": 6,
    },
]

# ── Category Rules ───────────────────────────────────────────────────────────
CATEGORY_RULES = [
    (["ransomware"],                                          "Ransomware",  "danger"),
    (["hack", "breach", "intrusion", "compromise"],          "Hacking",     "warning"),
    (["vulnerab", "cve", "zero-day", "0-day"],               "Vulnerability","info"),
    (["phish", "spear-phish", "smish"],                      "Phishing",    "warning"),
    (["malware", "trojan", "virus", "worm", "spyware"],      "Malware",     "danger"),
    (["patch", "update", "fix", "hotfix"],                   "Patch",       "success"),
    (["privacy", "leak", "data breach", "personal data"],    "Data Leak",   "danger"),
    (["ddos", "botnet"],                                      "DDoS",        "warning"),
    (["zero-day", "0-day"],                                   "Zero-Day",    "danger"),
]


def categorize(title: str, summary: str = "") -> dict:
    text = (title + " " + summary).lower()
    for keywords, label, level in CATEGORY_RULES:
        if any(k.lower() in text for k in keywords):
            return {"label": label, "level": level}
    return {"label": "Security News", "level": "info"}


def parse_date(entry) -> str:
    for attr in ("published", "updated", "created"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt.astimezone(timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


def clean_text(text: str, max_len: int = 200) -> str:
    import re
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] + "…" if len(text) > max_len else text


# ── AI Summary (Claude Haiku) ────────────────────────────────────────────────
def generate_ai_summary(title: str, summary: str = "") -> dict | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    prompt = f"""Analyze this security article and return JSON only. No markdown, pure JSON.

Title: {title}
Content: {summary[:400] if summary else "none"}

Return format:
{{"keypoints":["point1","point2"],"analysis":[{{"title":"subtitle1","desc":"desc1"}},{{"title":"subtitle2","desc":"desc2"}},{{"title":"subtitle3","desc":"desc3"}}],"suggested_title":"rewritten title"}}

Rules:
- keypoints: 2 items. No copy-paste from source. Compress to noun phrases. Max 10 words each.
  Good: "1M user records exposed", "Patch available — update now"
  Bad: "Researchers discovered a new vulnerability affecting millions of users worldwide" (copy-paste)
- analysis: 3 items. Each: subtitle (max 5 words) + desc (max 20 words). Draft for editor review.
- suggested_title: Rewrite for SEO. Max 12 words. Key keyword first.
  Good: "Critical Chrome Zero-Day Exploited — Update Immediately"
  Bad: "Google releases emergency patch for Chrome browser addressing critical zero-day vulnerability" (too long)
- All in English."""

    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=20,
            )
            resp.raise_for_status()
            text = resp.json()["content"][0]["text"]
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            print(f"    ⚠ AI summary failed (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2)
    return None


def fetch_source(source: dict) -> list:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        )
    }
    print(f"  ▶ [Priority {source['priority']}] {source['name']} fetching...")
    try:
        resp = requests.get(source["url"], headers=headers, timeout=12)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"    ✗ Failed: {e}")
        return []

    articles = []
    for entry in feed.entries[:15]:
        title    = clean_text(entry.get("title", "No title"), 120)
        link     = entry.get("link", "#")
        summary  = clean_text(
            entry.get("summary", "") or entry.get("description", ""), 400
        )
        pub_date = parse_date(entry)
        cat      = categorize(title, summary)
        uid      = hashlib.md5(link.encode()).hexdigest()[:12]

        ai = generate_ai_summary(title, summary)
        time.sleep(1.5)

        articles.append({
            "id":         uid,
            "title":      title,
            "link":       link,
            "summary":    summary,
            "pubDate":    pub_date,
            "source":     source["name"],
            "lang":       "en",
            "category":   cat["label"],
            "level":      cat["level"],
            "ai_summary": ai,
            # ai_summary structure:
            # {
            #   "keypoints":       ["noun phrase 1", "noun phrase 2"],
            #   "analysis":        [{"title":"subtitle","desc":"desc"}, ...],
            #   "suggested_title": "SEO-optimized title draft"
            # }
        })

    print(f"    ✓ {len(articles)} articles fetched")
    return articles


def deduplicate(articles: list) -> list:
    seen_ids    = set()
    seen_titles = set()
    result      = []
    for a in articles:
        title_key = a["title"][:20].strip()
        if a["id"] not in seen_ids and title_key not in seen_titles:
            seen_ids.add(a["id"])
            seen_titles.add(title_key)
            result.append(a)
        else:
            print(f"    ↳ Duplicate removed: {a['title'][:50]}")
    return result


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    print("\n══ SecurityRadar News Fetcher (EN) Started ══")
    print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   AI Summary: {'✓ Active (Claude Haiku)' if api_key else '✗ Inactive (no ANTHROPIC_API_KEY)'}\n")

    all_articles = []
    for src in sorted(RSS_SOURCES, key=lambda x: x["priority"]):
        articles = fetch_source(src)
        all_articles.extend(articles)
        time.sleep(1)

    unique = deduplicate(all_articles)
    unique.sort(key=lambda x: x["pubDate"], reverse=True)

    output = {
        "updated":  datetime.now(timezone.utc).isoformat(),
        "count":    len(unique[:30]),
        "lang":     "en",
        "articles": unique[:30],
    }

    with open("news_data_en.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    ai_count = sum(1 for a in unique[:30] if a.get("ai_summary"))
    print(f"\n══ Done ══")
    print(f"   Total fetched: {len(all_articles)} → after dedup: {len(unique)}")
    print(f"   AI summaries: {ai_count}")
    print(f"   Saved: news_data_en.json ({len(unique[:30])} articles)")


if __name__ == "__main__":
    main()
