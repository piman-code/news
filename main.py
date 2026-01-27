import feedparser
import datetime
from datetime import timedelta, timezone
import re
import trafilatura
import os

# ---------------------------------------------------------
# 1. 한국 시간(KST) 설정
# ---------------------------------------------------------
KST = timezone(timedelta(hours=9))

def get_korea_time():
    return datetime.datetime.now(KST)

# ---------------------------------------------------------
# 2. 뉴스 본문 요약 (가독성 개선: 줄바꿈 추가)
# ---------------------------------------------------------
def get_clean_summary(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None: return None

        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
        if not text or len(text) < 50: return None

        # 텍스트 정제
        text = text.replace('\n', ' ').strip()
        text = re.sub(r'\s+', ' ', text)

        # 문장 분리
        sentences = text.split('. ')
        summary_sentences = []
        char_count = 0
        
        for sent in sentences:
            clean_sent = sent.strip()
            if len(clean_sent) < 20: continue
            if not clean_sent.endswith('.'): clean_sent += '.'
            
            # 가독성을 위해 문장 앞에 인용구(>) 추가 및 줄바꿈 처리
            summary_sentences.append(f"> {clean_sent}")
            char_count += len(clean_sent)
            
            if char_count > 300: break # 핵심 3~4문장만
        
        # 줄바꿈(\n>)으로 연결하여 가독성 확보
        return '\n'.join(summary_sentences) if summary_sentences else None

    except Exception:
        return None

# ---------------------------------------------------------
# 3. 메인 로직
# ---------------------------------------------------------
def fetch_news():
    sources = {
        "🤖 인공지능 (AI)": "http://www.aitimes.com/rss/allArticle.xml",
        "💰 경제": "https://www.hankyung.com/feed/economy", 
        "🎓 교육": "http://www.veritas-a.com/rss/allArticle.xml" 
    }
    
    now = get_korea_time()
    today_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%I:%M:%S")
    
    # 오전/오후 태그 설정
    time_tag = "오전" if now.hour < 12 else "오후"
    
    # 전체 뉴스 컨텐츠를 담을 변수
    news_content = ""
    # 제목에 사용할 대표 키워드 리스트
    headlines = []

    # 뉴스 수집 시작
    for category, rss_url in sources.items():
        news_content += f"## {category}\n"
        
        try:
            feed = feedparser.parse(rss_url)
            # 카테고리별 첫 번째 기사 제목을 헤드라인 후보로 저장
            if feed.entries:
                first_title = feed.entries[0].title
                # 너무 긴 제목은 15자로 자름
                short_title = first_title[:15] + "..." if len(first_title) > 15 else first_title
                headlines.append(short_title)

            # 기사 3개씩 추출
            for entry in feed.entries[:3]:
                summary = get_clean_summary(entry.link)
                
                # 본문 요약 실패 시 RSS 설명 사용
                if not summary:
                    desc = entry.get('description', '요약 없음')
                    desc = re.sub(r'<[^>]+>', '', desc)[:100] + "..."
                    summary = f"> {desc}"
                
                news_content += f"### 🔗 [{entry.title}]({entry.link})\n"
                news_content += f"{summary}\n\n" # 요약문 (줄바꿈 적용됨)
                
        except Exception as e:
            news_content += f"> ⚠️ 뉴스 수집 에러: {e}\n\n"

    # ---------------------------------------------------------
    # 4. 파일 제목 및 Frontmatter 생성
    # ---------------------------------------------------------
    
    # 헤드라인 생성 (예: AI 혁명... / 금리 인상... / 수능 개편...)
    headline_str = " / ".join(headlines) if headlines else "주요 뉴스 브리핑"
    
    # Frontmatter 작성 (옵시디언용 메타데이터)
    frontmatter = f"""---
date: {today_str}
time: "{time_str}"
type: insight
tags: [뉴스, {time_tag}, AI, 경제, 교육]
created_at: "{today_str} {time_str} +09:00"
---

# 📅 {today_str} {time_tag} 브리핑: {headline_str}

"""
    
    # 최종 본문 결합
    final_content = frontmatter + news_content
    final_content += "---\n"
    final_content += f"✅ **최종 업데이트(한국시간):** {today_str} {time_str}\n"

    # 파일명 생성 (예: 2026-01-27_오전_Daily_News_Briefing.md)
    filename = f"{today_str}_{time_tag}_{time_str}_Daily_News_Briefing.md"
    
    return filename, final_content

if __name__ == "__main__":
    filename, content = fetch_news()
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"File Created: {filename}")
