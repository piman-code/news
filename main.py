import feedparser
import datetime
import re
import trafilatura

def get_clean_summary(url):
    try:
        # User-Agent를 브라우저처럼 위장하여 차단 방지
        downloaded = trafilatura.fetch_url(url)
        
        if downloaded is None:
            return ""

        # 본문 추출
        text = trafilatura.extract(downloaded, include_comments=False)
        
        if not text or len(text) < 50:
            return ""

        # 텍스트 정제
        text = text.replace('\n', ' ').strip()
        text = re.sub(r'\s+', ' ', text)

        # 문장 분리 및 요약
        sentences = text.split('. ')
        summary_sentences = []
        char_count = 0
        
        for sent in sentences:
            clean_sent = sent.strip()
            if len(clean_sent) < 15: continue
            
            if not clean_sent.endswith('.'):
                clean_sent += '.'
            
            summary_sentences.append(clean_sent)
            char_count += len(clean_sent)
            
            if char_count > 300: # 요약 길이 최적화
                break
        
        return ' '.join(summary_sentences) if summary_sentences else ""

    except Exception as e:
        print(f"Error: {url} -> {e}")
        return ""

def fetch_news():
    # 교육 소스를 더 안정적인 '베리타스알파'로 변경했습니다.
    sources = {
        "🤖 인공지능 (AI)": "http://www.aitimes.com/rss/allArticle.xml",
        "💰 경제": "https://www.hankyung.com/feed/economy", 
        "🎓 교육": "http://www.veritas-a.com/rss/allArticle.xml" 
    }
    
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    
    markdown = f"""---
date: {today_str}
last_update: {now.strftime("%Y-%m-%d %H:%M:%S")}
type: insight
topic: [인공지능, 경제, 교육]
tags: [뉴스, 요약, {today_str}]
source: [AI타임스, 한국경제, 베리타스알파]
---

# 📅 {now.strftime('%Y년 %m월 %d일(%a)')} 핵심 뉴스 브리핑

"""
    
    for category, rss_url in sources.items():
        markdown += f"## {category}\n"
        try:
            feed = feedparser.parse(rss_url)
            success_count = 0
            
            for entry in feed.entries:
                if success_count >= 2: break
                
                summary = get_clean_summary(entry.link)
                
                if not summary:
                    continue
                
                markdown += f"### 🔗 [{entry.title}]({entry.link})\n"
                markdown += f"> {summary}\n\n"
                success_count += 1
                
        except Exception:
            markdown += "> 해당 분야의 뉴스를 가져오지 못했습니다.\n\n"

    markdown += "---\n### 📂 자동화 기록 안내\n"
    markdown += f"최종 업데이트 시각: **{now.strftime('%Y-%m-%d %H:%M:%S')}**\n"

    # 현재 시간 가져오기
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    hour = now.hour

    # 시간대에 따라 오전/오후 구분
    if hour < 12:
        time_tag = "오전"
    else:
        time_tag = "오후"

    # 파일명 규칙: [2026-01-27]_[오전]_Daily_News_Briefing.md
    # filename = f"{today_str}_{time_tag}_Daily_News_Briefing.md"
    
    # 또는 더 구체적인 시간을 원하신다면:
    filename = f"{today_str}_{now.strftime('%H시%M분')}_Daily_News_Briefing.md"
    
    return filename, markdown

if __name__ == "__main__":
    filename, content = fetch_news()
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Created: {filename}")
