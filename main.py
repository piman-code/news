import feedparser
import datetime
import os
import re

def fetch_news():
    # 세계적 공신력을 가진 국내외 매체 RSS
    feeds = {
        "Global_AI_Tech": "https://www.technologyreview.com/feed/", # MIT 테크놀로지 리뷰
        "Global_Economy": "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml", # 뉴욕타임즈 경제
        "Education_KR": "https://www.hangyo.com/rss/allArticle.xml", # 한국교육신문
        "General_KR": "https://www.yna.co.kr/rss/news.xml" # 연합뉴스 종합
    }
    
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    today_with_day = now.strftime("%Y-%m-%d(%a)")
    
    content = f"""---
date: {today_str}
type: insight
tags: [AI, 교육, 글로벌, 경제]
source: [MIT_Tech, NYT, 한교신문, 연합뉴스]
---

# 📅 {today_with_day} 글로벌 쿼리티 뉴스 브리핑

공신력 있는 국내외 매체를 통해 수집된 최신 뉴스 요약입니다.

"""
    
    first_title = ""

    for category, url in feeds.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries: continue
            
            content += f"## 📌 {category}\n"
            for entry in feed.entries[:3]:
                # 요약 내용 정제
                summary = re.sub('<[^<]+?>', '', entry.description) if 'description' in entry else ""
                summary = summary.strip()[:200]
                
                content += f"### {entry.title}\n"
                content += f"- **요약:** {summary}...\n"
                content += f"- [출처 원문 보기]({entry.link})\n\n"
                
                if not first_title:
                    # 파일명용: 한글, 영문, 숫자만 허용
                    first_title = re.sub(r'[^가-힣a-zA-Z0-9\s]', '', entry.title).strip()[:25]
        except Exception as e:
            print(f"Error fetching {category}: {e}")

    # 파일명 결정 (공백을 언더바로 교체)
    safe_title = first_title.replace(" ", "_")
    filename = f"{today_str}_{safe_title}.md"
    return filename, content

if __name__ == "__main__":
    filename, content = fetch_news()
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Success: {filename} created.")
