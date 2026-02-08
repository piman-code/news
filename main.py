import datetime
import glob
import math
import html
import json
import os
import re
import ssl
import time
import urllib.request
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import trafilatura

try:
    import certifi
except ImportError:
    certifi = None

# ---------------------------------------------------------
# 1. 기본 설정
# ---------------------------------------------------------
KST = timezone(timedelta(hours=9))

MAX_ITEMS_PER_CATEGORY = int(os.getenv("MAX_ITEMS_PER_CATEGORY", "5"))
MAX_FEED_ITEMS_PER_SOURCE = int(os.getenv("MAX_FEED_ITEMS_PER_SOURCE", "12"))
MAX_ITEMS_PER_DOMAIN_PER_CATEGORY = int(
    os.getenv("MAX_ITEMS_PER_DOMAIN_PER_CATEGORY", "1")
)
MAX_ITEMS_PER_SOURCE = int(os.getenv("MAX_ITEMS_PER_SOURCE", "2"))
SUMMARY_CHAR_LIMIT = int(os.getenv("SUMMARY_CHAR_LIMIT", "320"))
FEED_TIMEOUT_SECONDS = int(os.getenv("FEED_TIMEOUT_SECONDS", "10"))
FEED_MAX_RETRIES = int(os.getenv("FEED_MAX_RETRIES", "2"))
HISTORY_WINDOW_FILES = int(os.getenv("HISTORY_WINDOW_FILES", "14"))
DOMAIN_REPEAT_PENALTY = float(os.getenv("DOMAIN_REPEAT_PENALTY", "0.45"))
DEBUG_FEED_ERRORS = os.getenv("DEBUG_FEED_ERRORS", "0") == "1"
TRANSLATE_FOREIGN_TO_KO = os.getenv("TRANSLATE_FOREIGN_TO_KO", "1") == "1"
TRANSLATION_PROVIDER = os.getenv("TRANSLATION_PROVIDER", "auto").lower()
TRANSLATE_TIMEOUT_SECONDS = int(os.getenv("TRANSLATE_TIMEOUT_SECONDS", "10"))
SHOW_ORIGINAL_TEXT = os.getenv("SHOW_ORIGINAL_TEXT", "1") == "1"
PAPAGO_CLIENT_ID = os.getenv("PAPAGO_CLIENT_ID", "")
PAPAGO_CLIENT_SECRET = os.getenv("PAPAGO_CLIENT_SECRET", "")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "🤖 인공지능 (AI)": [
        "ai",
        "인공지능",
        "생성형",
        "llm",
        "agent",
        "model",
        "semiconductor",
        "chip",
        "robot",
    ],
    "💰 경제": [
        "경제",
        "금리",
        "inflation",
        "gdp",
        "환율",
        "stock",
        "market",
        "실적",
        "무역",
        "recession",
    ],
    "🎓 교육": [
        "교육",
        "대학",
        "school",
        "student",
        "edtech",
        "learning",
        "교사",
        "입시",
        "curriculum",
    ],
}

NOISE_PATTERNS = [
    r"기사\s*스크랩.*?프린트",
    r"\[베리타스알파=.*?기자\]",
]

TRACKING_QUERY_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ocid",
    "spm",
    "ref",
    "ref_src",
}

TRANSLATION_CACHE: Dict[str, str] = {}


@dataclass(frozen=True)
class FeedSource:
    name: str
    url: str
    weight: float = 1.0


@dataclass
class ArticleCandidate:
    category: str
    title: str
    link: str
    canonical_link: str
    normalized_title: str
    summary_hint: str
    source_name: str
    domain: str
    published_at: Optional[datetime.datetime]
    score: float


NEWS_SOURCES: Dict[str, List[FeedSource]] = {
    "🤖 인공지능 (AI)": [
        FeedSource("AI타임스", "https://www.aitimes.com/rss/allArticle.xml", 1.0),
        FeedSource(
            "MIT Technology Review AI",
            "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
            1.2,
        ),
        FeedSource("VentureBeat AI", "https://venturebeat.com/category/ai/feed/", 1.1),
        FeedSource("Wired AI", "https://www.wired.com/feed/tag/ai/latest/rss", 1.1),
        FeedSource(
            "AI News",
            "https://www.artificialintelligence-news.com/feed/",
            1.0,
        ),
        FeedSource(
            "The Verge AI",
            "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
            1.0,
        ),
    ],
    "💰 경제": [
        FeedSource("한국경제", "https://www.hankyung.com/feed/economy", 1.0),
        FeedSource("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", 1.1),
        FeedSource(
            "NYT Economy",
            "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
            1.1,
        ),
        FeedSource(
            "CNBC Economy",
            "https://www.cnbc.com/id/10001147/device/rss/rss.html",
            1.0,
        ),
        FeedSource("MarketWatch", "https://www.marketwatch.com/rss/topstories", 0.95),
        FeedSource("NPR Economy", "https://feeds.npr.org/1006/rss.xml", 1.0),
    ],
    "🎓 교육": [
        FeedSource("베리타스알파", "https://www.veritas-a.com/rss/allArticle.xml", 1.0),
        FeedSource("Higher Ed Dive", "https://www.highereddive.com/feeds/news/", 1.1),
        FeedSource("BBC Education", "https://feeds.bbci.co.uk/news/education/rss.xml", 1.0),
        FeedSource("eCampus News", "https://www.ecampusnews.com/feed/", 1.0),
        FeedSource(
            "UK Department for Education",
            "https://www.gov.uk/government/organisations/department-for-education.atom",
            0.9,
        ),
    ],
}


def get_korea_time() -> datetime.datetime:
    return datetime.datetime.now(KST)


def get_ssl_context():
    if certifi:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def contains_korean(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text))


def is_translation_candidate(text: str) -> bool:
    if not text or not TRANSLATE_FOREIGN_TO_KO:
        return False
    if contains_korean(text):
        return False
    return bool(re.search(r"[A-Za-z]", text))


def call_papago_translate(text: str) -> Optional[str]:
    if not PAPAGO_CLIENT_ID or not PAPAGO_CLIENT_SECRET:
        return None

    payload = urlencode(
        {
            "source": "en",
            "target": "ko",
            "text": text,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        "https://openapi.naver.com/v1/papago/n2mt",
        data=payload,
        headers={
            "X-Naver-Client-Id": PAPAGO_CLIENT_ID,
            "X-Naver-Client-Secret": PAPAGO_CLIENT_SECRET,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": USER_AGENT,
        },
    )

    with urllib.request.urlopen(
        request, timeout=TRANSLATE_TIMEOUT_SECONDS, context=get_ssl_context()
    ) as response:
        raw = response.read().decode("utf-8", errors="ignore")

    parsed = json.loads(raw)
    return (
        parsed.get("message", {})
        .get("result", {})
        .get("translatedText", "")
        .strip()
        or None
    )


def call_public_google_translate(text: str) -> Optional[str]:
    query = urlencode(
        {
            "client": "gtx",
            "sl": "auto",
            "tl": "ko",
            "dt": "t",
            "q": text,
        }
    )

    url = "https://translate.googleapis.com/translate_a/single?{}".format(query)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    with urllib.request.urlopen(
        request, timeout=TRANSLATE_TIMEOUT_SECONDS, context=get_ssl_context()
    ) as response:
        raw = response.read().decode("utf-8", errors="ignore")

    parsed = json.loads(raw)
    chunks = parsed[0] if isinstance(parsed, list) and parsed else []
    translated = "".join(
        item[0] for item in chunks if isinstance(item, list) and item and item[0]
    ).strip()
    return translated or None


def translate_text_to_korean(text: str) -> str:
    if not is_translation_candidate(text):
        return text

    cached = TRANSLATION_CACHE.get(text)
    if cached:
        return cached

    translated = None

    try:
        if TRANSLATION_PROVIDER in ("papago", "auto"):
            translated = call_papago_translate(text)
    except Exception:
        translated = None

    if not translated:
        try:
            if TRANSLATION_PROVIDER in ("google", "auto"):
                translated = call_public_google_translate(text)
        except Exception:
            translated = None

    if not translated:
        translated = text

    TRANSLATION_CACHE[text] = translated
    return translated


def translate_title_for_output(title: str) -> str:
    translated = translate_text_to_korean(title)
    if translated != title and SHOW_ORIGINAL_TEXT:
        return "{} ({})".format(translated, title)
    return translated


def translate_summary_to_korean(summary: str) -> str:
    if not summary:
        return summary

    translated_lines: List[str] = []
    for line in summary.splitlines():
        stripped = line.strip()
        if not stripped:
            translated_lines.append(line)
            continue

        is_quote = stripped.startswith(">")
        body = stripped[1:].strip() if is_quote else stripped
        translated_body = translate_text_to_korean(body)

        if translated_body != body and SHOW_ORIGINAL_TEXT:
            merged = "{} (원문: {})".format(translated_body, body)
        else:
            merged = translated_body

        translated_lines.append("> " + merged if is_quote else merged)

    return "\n".join(translated_lines)


def fetch_feed_entries(feed_url: str):
    request = urllib.request.Request(feed_url, headers={"User-Agent": USER_AGENT})
    context = get_ssl_context()
    last_error = None

    for attempt in range(1, FEED_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(
                request,
                timeout=FEED_TIMEOUT_SECONDS,
                context=context,
            ) as response:
                payload = response.read()
            parsed = feedparser.parse(payload)
            entries = list(getattr(parsed, "entries", []))
            if entries:
                return entries
        except Exception as exc:
            last_error = exc
            time.sleep(0.5 * attempt)

    fallback = feedparser.parse(feed_url)
    fallback_entries = list(getattr(fallback, "entries", []))
    if fallback_entries:
        return fallback_entries

    if DEBUG_FEED_ERRORS:
        bozo_exc = getattr(fallback, "bozo_exception", None)
        print(
            "[WARN] feed failed:",
            feed_url,
            "| last_error:",
            last_error,
            "| bozo:",
            bozo_exc,
        )

    return []


def compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_html(raw_text: str) -> str:
    if not raw_text:
        return ""
    text = html.unescape(raw_text)
    text = re.sub(r"<[^>]+>", " ", text)
    return compact_whitespace(text)


def remove_noise(text: str) -> str:
    cleaned = text
    for pattern in NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    return compact_whitespace(cleaned)


def normalize_title(title: str) -> str:
    text = remove_noise(strip_html(title).lower())
    text = re.sub(r"[^\w\s가-힣]", " ", text)
    return compact_whitespace(text)


def canonicalize_url(url: str) -> str:
    if not url:
        return ""

    parsed = urlparse(url)
    if not parsed.netloc:
        return ""

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lowered_key = key.lower()
        if lowered_key.startswith("utm_"):
            continue
        if lowered_key in TRACKING_QUERY_PARAMS:
            continue
        query_items.append((key, value))

    query = urlencode(sorted(query_items))
    normalized = parsed._replace(fragment="", query=query)
    return urlunparse(normalized)


def extract_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            return netloc[4:]
        return netloc
    except Exception:
        return ""


def resolve_entry_link(entry) -> str:
    link = entry.get("link", "")
    if not link:
        return ""

    if "news.google.com" in link:
        summary = entry.get("summary", "") or entry.get("description", "")
        match = re.search(r'href=["\'](https?://[^"\']+)["\']', summary)
        if match and "news.google.com" not in match.group(1):
            return html.unescape(match.group(1))

    return link


def parse_entry_datetime(entry) -> Optional[datetime.datetime]:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            try:
                dt_utc = datetime.datetime(*value[:6], tzinfo=datetime.timezone.utc)
                return dt_utc.astimezone(KST)
            except Exception:
                continue
    return None


def load_recent_domain_counts() -> Dict[str, int]:
    files = sorted(glob.glob("*_Daily_News_Briefing.md"), reverse=True)
    recent_files = files[:HISTORY_WINDOW_FILES]
    counts: Dict[str, int] = {}

    for path in recent_files:
        try:
            with open(path, "r", encoding="utf-8") as file:
                content = file.read()
        except Exception:
            continue

        links = re.findall(r"\((https?://[^)\s]+)\)", content)
        for link in links:
            domain = extract_domain(link)
            if not domain:
                continue
            counts[domain] = counts.get(domain, 0) + 1

    return counts


def recency_score(published_at: Optional[datetime.datetime], now: datetime.datetime) -> float:
    if not published_at:
        return 0.3

    age_hours = max((now - published_at).total_seconds() / 3600, 0)
    if age_hours <= 6:
        return 2.3
    if age_hours <= 24:
        return 1.6
    if age_hours <= 48:
        return 1.0
    if age_hours <= 96:
        return 0.4
    return -0.2


def relevance_score(category: str, title: str, summary_hint: str) -> float:
    haystack = (title + " " + summary_hint).lower()
    score = 0.0
    for keyword in CATEGORY_KEYWORDS.get(category, []):
        if keyword.lower() in haystack:
            score += 0.7
    return min(score, 3.5)


def quality_score(title: str, summary_hint: str) -> float:
    score = 0.0
    title_len = len(title)

    if 18 <= title_len <= 120:
        score += 0.4
    elif title_len < 10:
        score -= 0.5
    elif title_len > 160:
        score -= 0.3

    if any(token in summary_hint for token in ["기사 스크랩", "클린뷰", "프린트"]):
        score -= 0.8

    return score


def domain_history_penalty(domain: str, domain_history: Dict[str, int]) -> float:
    if not domain:
        return 0.0

    # 빈번하게 반복된 도메인은 로그 스케일로 완만하게 감점
    repeats = domain_history.get(domain, 0)
    return min(math.log1p(repeats) * DOMAIN_REPEAT_PENALTY, 1.6)


def score_entry(
    category: str,
    title: str,
    summary_hint: str,
    source_weight: float,
    published_at: Optional[datetime.datetime],
    domain: str,
    domain_history: Dict[str, int],
    now: datetime.datetime,
) -> float:
    return (
        source_weight
        + recency_score(published_at, now)
        + relevance_score(category, title, summary_hint)
        + quality_score(title, summary_hint)
        - domain_history_penalty(domain, domain_history)
    )


def extract_summary_sentences(text: str, max_sentences: int, max_chars: int) -> Optional[str]:
    cleaned_text = remove_noise(compact_whitespace(text))
    if not cleaned_text:
        return None

    raw_sentences = re.split(r"(?<=[.!?])\s+", cleaned_text)
    summary_lines: List[str] = []
    seen = set()
    char_count = 0

    for sentence in raw_sentences:
        sentence = compact_whitespace(sentence.strip(" -|•"))
        if len(sentence) < 18:
            continue

        norm = normalize_title(sentence)
        if norm in seen:
            continue
        seen.add(norm)

        if not re.search(r"[.!?]$", sentence):
            sentence += "."

        if char_count + len(sentence) > max_chars and summary_lines:
            break

        summary_lines.append("> " + sentence)
        char_count += len(sentence)

        if len(summary_lines) >= max_sentences:
            break

    if not summary_lines:
        return None
    return "\n".join(summary_lines)


def get_clean_summary(url: str, fallback_text: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
            )
            if text and len(text) >= 80:
                summary = extract_summary_sentences(
                    text.replace("\n", " "),
                    max_sentences=4,
                    max_chars=SUMMARY_CHAR_LIMIT,
                )
                if summary:
                    return summary
    except Exception:
        pass

    fallback = extract_summary_sentences(
        strip_html(fallback_text), max_sentences=2, max_chars=220
    )
    return fallback if fallback else "> 요약 없음."


def collect_candidates(
    category: str,
    feed_sources: List[FeedSource],
    domain_history: Dict[str, int],
    now: datetime.datetime,
) -> List[ArticleCandidate]:
    candidates: List[ArticleCandidate] = []

    for source in feed_sources:
        entries = fetch_feed_entries(source.url)[:MAX_FEED_ITEMS_PER_SOURCE]

        for entry in entries:
            title = compact_whitespace(entry.get("title", ""))
            if not title:
                continue

            link = resolve_entry_link(entry)
            if not link:
                continue

            canonical_link = canonicalize_url(link)
            normalized_title = normalize_title(title)
            summary_hint = strip_html(entry.get("summary") or entry.get("description", ""))
            published_at = parse_entry_datetime(entry)
            domain = extract_domain(link)

            score = score_entry(
                category=category,
                title=title,
                summary_hint=summary_hint,
                source_weight=source.weight,
                published_at=published_at,
                domain=domain,
                domain_history=domain_history,
                now=now,
            )

            candidates.append(
                ArticleCandidate(
                    category=category,
                    title=title,
                    link=link,
                    canonical_link=canonical_link,
                    normalized_title=normalized_title,
                    summary_hint=summary_hint,
                    source_name=source.name,
                    domain=domain,
                    published_at=published_at,
                    score=score,
                )
            )

    return candidates


def dedupe_candidates(candidates: List[ArticleCandidate]) -> List[ArticleCandidate]:
    if not candidates:
        return []

    min_dt = datetime.datetime(1970, 1, 1, tzinfo=KST)
    sorted_candidates = sorted(
        candidates,
        key=lambda item: (item.score, item.published_at or min_dt),
        reverse=True,
    )

    deduped: List[ArticleCandidate] = []
    seen_links = set()
    seen_titles = set()

    for candidate in sorted_candidates:
        link_key = candidate.canonical_link or candidate.link
        title_key = candidate.normalized_title

        if link_key and link_key in seen_links:
            continue
        if title_key and title_key in seen_titles:
            continue

        deduped.append(candidate)
        if link_key:
            seen_links.add(link_key)
        if title_key:
            seen_titles.add(title_key)

    return deduped


def select_diverse_articles(candidates: List[ArticleCandidate], limit: int) -> List[ArticleCandidate]:
    selected: List[ArticleCandidate] = []
    selected_keys = set()
    domain_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}

    for candidate in candidates:
        if len(selected) >= limit:
            break

        if domain_counts.get(candidate.domain, 0) >= MAX_ITEMS_PER_DOMAIN_PER_CATEGORY:
            continue
        if source_counts.get(candidate.source_name, 0) >= MAX_ITEMS_PER_SOURCE:
            continue

        key = candidate.canonical_link or candidate.normalized_title
        if key in selected_keys:
            continue

        selected.append(candidate)
        selected_keys.add(key)
        domain_counts[candidate.domain] = domain_counts.get(candidate.domain, 0) + 1
        source_counts[candidate.source_name] = source_counts.get(candidate.source_name, 0) + 1

    # 도메인 제한으로 기사 수가 부족하면 남은 점수 순으로 보충
    if len(selected) < limit:
        for candidate in candidates:
            if len(selected) >= limit:
                break
            key = candidate.canonical_link or candidate.normalized_title
            if key in selected_keys:
                continue
            selected.append(candidate)
            selected_keys.add(key)

    return selected


# ---------------------------------------------------------
# 2. 메인 로직
# ---------------------------------------------------------
def fetch_news():
    now = get_korea_time()
    today_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%I:%M:%S")
    time_tag = "오전" if now.hour < 12 else "오후"

    news_content = ""
    headlines: List[str] = []
    globally_seen = set()
    domain_history = load_recent_domain_counts()

    for category, feed_sources in NEWS_SOURCES.items():
        news_content += "## {}\n".format(category)

        candidates = collect_candidates(category, feed_sources, domain_history, now)
        candidates = dedupe_candidates(candidates)
        selected = select_diverse_articles(candidates, MAX_ITEMS_PER_CATEGORY)

        if selected:
            headline_base = translate_text_to_korean(selected[0].title)
            headlines.append(headline_base[:15] + "..." if len(headline_base) > 15 else headline_base)
        else:
            news_content += "> ⚠️ 수집된 기사 없음\n\n"
            continue

        for article in selected:
            global_key = article.canonical_link or article.normalized_title
            if global_key in globally_seen:
                continue
            globally_seen.add(global_key)

            summary = get_clean_summary(article.link, article.summary_hint)
            summary = translate_summary_to_korean(summary)

            domain_label = article.domain or "unknown"
            title_for_output = translate_title_for_output(article.title)
            news_content += "### 🔗 [{}]({})\n".format(title_for_output, article.link)
            news_content += "> 출처: {} ({})\n".format(article.source_name, domain_label)
            news_content += summary + "\n\n"

    headline_str = " / ".join(headlines) if headlines else "주요 뉴스 브리핑"

    frontmatter = f"""---
date: {today_str}
time: \"{time_str}\"
type: insight
tags: [뉴스, {time_tag}, AI, 경제, 교육]
created_at: \"{today_str} {time_str}\"
---

# 📅 {today_str} {time_tag} 브리핑: {headline_str}

"""

    final_content = frontmatter + news_content
    final_content += "---\n"
    final_content += "✅ **최종 업데이트(한국시간):** {} {}\n".format(today_str, time_str)

    filename = "{}_{}_{}_Daily_News_Briefing.md".format(today_str, time_tag, time_str)
    return filename, final_content


if __name__ == "__main__":
    filename, content = fetch_news()
    with open(filename, "w", encoding="utf-8") as file:
        file.write(content)
    print("File Created: {}".format(filename))
