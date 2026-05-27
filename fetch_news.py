"""
보안레이더 — 한국어 뉴스 자동 수집 + AI 요약 스크립트
=====================================================
사용법:
  1. 로컬 실행:  python fetch_news.py
  2. GitHub Actions (무료 자동화):
     - 이 파일을 GitHub 저장소에 업로드
     - .github/workflows/fetch_news.yml 파일 생성 (아래 주석 참고)
     - 매일 한국시간 오전 10시 자동 실행
     - GitHub Settings → Secrets → ANTHROPIC_API_KEY 등록

출력: news_data.json (HTML 페이지에서 fetch하여 사용)

[GitHub Actions 워크플로우]
--------------------------------------------------
# .github/workflows/fetch_news.yml
name: 보안 뉴스 자동 수집

on:
  schedule:
    - cron: '0 1 * * *'   # 매일 한국시간 오전 10시 (UTC 01:00)
  workflow_dispatch:        # 수동 실행 가능

jobs:
  fetch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install requests feedparser anthropic
      - run: python fetch_news.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - name: 결과 커밋
        run: |
          git config user.name "news-bot"
          git config user.email "bot@example.com"
          git add news_data.json
          git diff --staged --quiet || git commit -m "뉴스 업데이트 $(date '+%Y-%m-%d %H:%M')"
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

# ── RSS 소스 ────────────────────────────────────────────────────────────────
RSS_SOURCES = [
    {
        "name": "보안뉴스",
        "url": "https://www.boannews.com/rss/rss_all.xml",
        "lang": "ko",
        "priority": 1,
    },
    {
        "name": "데일리시큐",
        "url": "https://www.dailysecu.com/rss/allArticle.xml",
        "lang": "ko",
        "priority": 2,
    },
    {
        "name": "Google 뉴스 — 보안 최신",
        "url": (
            "https://news.google.com/rss/search"
            "?q=보안+해킹+사이버&hl=ko&gl=KR&ceid=KR:ko"
        ),
        "lang": "ko",
        "priority": 3,
    },
    {
        "name": "Google 뉴스 — 랜섬웨어",
        "url": (
            "https://news.google.com/rss/search"
            "?q=랜섬웨어+악성코드+취약점&hl=ko&gl=KR&ceid=KR:ko"
        ),
        "lang": "ko",
        "priority": 4,
    },
    {
        "name": "KISA 보호나라",
        "url": "https://www.kisa.or.kr/rss/rss.jsp",
        "lang": "ko",
        "priority": 5,
    },
]

# ── 카테고리 규칙 ────────────────────────────────────────────────────────────
CATEGORY_RULES = [
    (["랜섬웨어", "ransomware"],                              "랜섬웨어",  "danger"),
    (["해킹", "hack", "breach", "침해", "침투"],              "해킹",      "warning"),
    (["취약점", "vulnerab", "cve", "zero-day", "0-day"],     "취약점",    "info"),
    (["피싱", "phish", "스미싱"],                             "피싱",      "warning"),
    (["malware", "악성코드", "trojan", "virus", "worm"],      "악성코드",  "danger"),
    (["patch", "패치", "업데이트", "fix", "update"],          "보안패치",  "success"),
    (["개인정보", "privacy", "leak", "유출", "data"],         "개인정보",  "danger"),
    (["ddos", "botnet"],                                      "DDoS",      "warning"),
    (["제로데이", "zero-day", "0-day"],                       "제로데이",  "danger"),
]


def categorize(title: str, summary: str = "") -> dict:
    text = (title + " " + summary).lower()
    for keywords, label, level in CATEGORY_RULES:
        if any(k.lower() in text for k in keywords):
            return {"label": label, "level": level}
    return {"label": "보안뉴스", "level": "info"}


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


# ── AI 요약 생성 (Claude Haiku) ──────────────────────────────────────────────
def generate_ai_summary(title: str, summary: str = "") -> dict | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None  # 키 없으면 건너뜀 (로컬 테스트용)

    prompt = f"""보안 기사를 분석해서 JSON만 반환하세요. 마크다운 없이 JSON만.

제목: {title}
내용: {summary[:300] if summary else "없음"}

{{"keypoints":["핵심 요점 첫째 문장","핵심 요점 둘째 문장"],"analysis":[{{"title":"소제목1","desc":"설명1"}},{{"title":"소제목2","desc":"설명2"}},{{"title":"소제목3","desc":"설명3"}}]}}

keypoints 2개, analysis 3개, 모두 한국어로."""

    for attempt in range(3):  # 최대 3회 재시도
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
            print(f"    ⚠ AI 요약 실패 (시도 {attempt+1}/3): {e}")
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
    print(f"  ▶ [{source['priority']}순위] {source['name']} 수집 중...")
    try:
        resp = requests.get(source["url"], headers=headers, timeout=12)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"    ✗ 실패: {e}")
        return []

    articles = []
    for entry in feed.entries[:15]:
        title   = clean_text(entry.get("title", "제목 없음"), 120)
        link    = entry.get("link", "#")
        summary = clean_text(
            entry.get("summary", "") or entry.get("description", ""), 400
        )
        pub_date = parse_date(entry)
        cat = categorize(title, summary)
        uid = hashlib.md5(link.encode()).hexdigest()[:12]

        # AI 요약 생성
        ai = generate_ai_summary(title, summary)
        time.sleep(1.5)  # API rate limit 방지

        articles.append({
            "id":         uid,
            "title":      title,
            "link":       link,
            "summary":    summary,
            "pubDate":    pub_date,
            "source":     source["name"],
            "lang":       "ko",
            "category":   cat["label"],
            "level":      cat["level"],
            "ai_summary": ai,  # {"keypoints": [...], "analysis": [...]} or null
        })

    print(f"    ✓ {len(articles)}건 수집")
    return articles


def deduplicate(articles: list) -> list:
    seen, result = set(), []
    for a in articles:
        if a["id"] not in seen:
            seen.add(a["id"])
            result.append(a)
    return result


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    print("\n══ 보안레이더 뉴스 수집기 시작 ══")
    print(f"   실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   AI 요약: {'✓ 활성 (Claude Haiku)' if api_key else '✗ 비활성 (ANTHROPIC_API_KEY 없음)'}\n")

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
        "lang":     "ko",
        "articles": unique[:30],
    }

    with open("news_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    ai_count = sum(1 for a in unique[:30] if a.get("ai_summary"))
    print(f"\n══ 완료 ══")
    print(f"   총 수집: {len(all_articles)}건 → 중복 제거 후 {len(unique)}건")
    print(f"   AI 요약 생성: {ai_count}건")
    print(f"   저장: news_data.json ({len(unique[:30])}건)")


if __name__ == "__main__":
    main()
