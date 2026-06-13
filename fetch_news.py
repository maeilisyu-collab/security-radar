# © 2026 SecurityRadar (securityradar.io). All rights reserved.
# Unauthorized copying or distribution is prohibited.
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
        "url": (
            "https://news.google.com/rss/search"
            "?q=KISA+보호나라+보안공지&hl=ko&gl=KR&ceid=KR:ko"
        ),
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

    # ★ 핵심 변경 포인트:
    # 1. keypoints: 원문 복붙 금지, 명사형 압축 (20자 이내)
    # 2. analysis: 운영자 편집용 3항목 초안
    # 3. suggested_title: SEO 최적화 제목 초안 (운영자 확인 후 사용)
    # 4. bottom_line: 에디터 관점의 한 줄 평 — 중립 금지, 입장 필수
    # 5. action_today: 지금 당장 할 수 있는 1분짜리 행동 하나
    prompt = f"""보안 기사를 분석해서 JSON만 반환하세요. 마크다운 없이 순수 JSON만.

제목: {title}
내용: {summary[:400] if summary else "없음"}

반환 형식:
{{"keypoints":["명사형압축1","명사형압축2"],"analysis":[{{"title":"소제목1","desc":"설명1"}},{{"title":"소제목2","desc":"설명2"}},{{"title":"소제목3","desc":"설명3"}}],"suggested_title":"재작성제목","bottom_line":"에디터한줄평","action_today":"오늘할일"}}

규칙:
- keypoints 2개: 원문 문장 절대 복붙 금지. 명사형으로 압축. 각 20자 이내.
  좋은 예: "본선 진출 확정", "개인정보 100만 건 유출", "패치 미적용 시 위험"
  나쁜 예: "엔키화이트햇 연구원들이 DEF CON CTF 2026 예선에서 상위권 성적을 거뒀다" (원문 복붙)
- analysis 3개: 각각 소제목(10자 이내) + 설명(50자 이내). 운영자가 편집할 초안.
- suggested_title: 원제목을 SEO에 맞게 재작성. 30자 이내. 핵심 키워드 앞에.
  좋은 예: "모젠코리아, AI 비서 탑재 플랫폼 출시"
  나쁜 예: "모젠코리아, 중앙 집중식 관리와 AI 비서 탑재한 BQN 플랫폼 R5.0 출시" (원제목 그대로)
- bottom_line: 에디터의 관점이 담긴 한 줄 평. 40자 이내. 반드시 입장을 취할 것.
  중립 요약 절대 금지. "~에 주의하세요" 패턴 금지. "~할 필요가 있다" 패턴 금지.
  독자가 읽고 "아, 이거 나한테 해당되는 얘기네" 또는 "소름돋는다" 반응이 나와야 함.
  좋은 예: "지금 당신 폰 유심이 이 기사의 주인공일 수 있다"
  좋은 예: "패치 안 하면 해커가 이미 들어와 있다는 뜻이다"
  좋은 예: "VPN 믿고 있다면, 이 기사 먼저 읽어야 한다"
  나쁜 예: "보안 업데이트의 중요성을 다시 한번 상기시켜준다" (중립 요약)
  나쁜 예: "사용자들의 각별한 주의가 필요하다" (관료적 문체)
- action_today: 지금 당장 실행 가능한 행동 하나. 40자 이내. 앱 이름·경로·동작을 명시.
  "설정 → 경로 → 행동" 형식 선호. 막연한 권고 절대 금지.
  좋은 예: "T월드 앱 → 유심 보호 서비스 → ON (3분)"
  좋은 예: "크롬 → 설정 → 개인정보 → WebRTC 차단 확인"
  좋은 예: "윈도우 설정 → Windows Update → 지금 업데이트"
  나쁜 예: "보안 소프트웨어를 최신 버전으로 업데이트 하세요" (막연)
  나쁜 예: "관련 기관에 문의하시기 바랍니다" (행동 불가)
- 모두 한국어로."""

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
                    "max_tokens": 1400,
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
            "ai_summary": ai,
            # ai_summary 구조:
            # {
            #   "keypoints":      ["명사형압축1", "명사형압축2"],          ← 핵심 포인트 표시용
            #   "analysis":       [{"title":"소제목","desc":"설명"}, ...], ← 3항목 초안
            #   "suggested_title":"SEO 최적화된 제목 초안",                ← 제목 편집창 기본값
            #   "bottom_line":    "에디터 관점 한 줄 평",                  ← 입장 필수, 중립 금지
            #   "action_today":   "오늘 할 일 하나 (앱→경로→동작)"         ← 1분 실행 가능 행동
            # }
        })

    print(f"    ✓ {len(articles)}건 수집")
    return articles


# ★ 중복 필터링 강화: URL(id) + 제목 앞 20자 동시 체크
def deduplicate(articles: list) -> list:
    seen_ids    = set()
    seen_titles = set()
    result      = []
    for a in articles:
        title_key = a["title"][:20].strip()  # 제목 앞 20자로 유사 중복 체크
        if a["id"] not in seen_ids and title_key not in seen_titles:
            seen_ids.add(a["id"])
            seen_titles.add(title_key)
            result.append(a)
        else:
            print(f"    ↳ 중복 제거: {a['title'][:40]}")
    return result


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    print("\n══ 보안레이더 뉴스 수집기 시작 ══")
    print(f"   실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   AI 요약: {'✓ 활성 (Claude Haiku)' if api_key else '✗ 비활성 (ANTHROPIC_API_KEY 없음)'}\n")

    # ── 기존 데이터 로드 (AI 요약 재사용 + 기사 합치기용) ──
    existing_articles = []
    if os.path.exists("news_data.json"):
        try:
            with open("news_data.json", "r", encoding="utf-8") as f:
                old_data = json.load(f)
                existing_articles = old_data.get("articles", [])
            print(f"   기존 기사: {len(existing_articles)}건 로드됨")
        except Exception as e:
            print(f"   기존 데이터 로드 실패: {e}")

    existing_map = {a["id"]: a for a in existing_articles}

    all_articles = []
    for src in sorted(RSS_SOURCES, key=lambda x: x["priority"]):
        articles = fetch_source(src)
        all_articles.extend(articles)
        time.sleep(1)

    unique = deduplicate(all_articles)

    # ── 기존 AI 요약 재사용 (이미 있는 기사는 API 호출 안 함) ──
    for a in unique:
        if not a.get("ai_summary") and a["id"] in existing_map:
            a["ai_summary"] = existing_map[a["id"]].get("ai_summary")

    # ── 새 기사 + 기존 기사 합치기 (기사가 같아도 updated 시간 갱신됨) ──
    new_ids = {a["id"] for a in unique}
    combined = unique + [a for a in existing_articles if a["id"] not in new_ids]
    combined.sort(key=lambda x: x["pubDate"], reverse=True)
    final_articles = combined[:30]

    output = {
        "updated":  datetime.now(timezone.utc).isoformat(),  # 항상 현재 시간
        "count":    len(final_articles),
        "lang":     "ko",
        "articles": final_articles,
    }

    with open("news_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    ai_count = sum(1 for a in final_articles if a.get("ai_summary"))
    print(f"\n══ 완료 ══")
    print(f"   총 수집: {len(all_articles)}건 → 중복 제거 후 {len(unique)}건")
    print(f"   AI 요약 생성: {ai_count}건")
    print(f"   저장: news_data.json ({len(unique[:30])}건)")


if __name__ == "__main__":
    main()
