import feedparser
import datetime
import os
import re
from googletrans import Translator # 무료 번역 라이브러리

def fetch_and_translate():
    translator = Translator()
    # 권위 있는 매체 리스트
    feeds = {
        "🤖 인공지능 (AI)": "https://www.technologyreview.com/feed/",
        "🏛️ 정치/경제": "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
        "🎓 교육": "https://www.hangyo.com/rss/allArticle.xml",
        "🏥 사회": "https://www.yna.co.kr/rss/news.xml"
    }
    
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    today_kr = now.strftime("%Y년 %m월 %d일(%a)")
    
    content = f"---\ndate: {today_str}\ntags: [뉴스, 요약, 자동화]\n---\n\n"
    content += f"# 📅 {today_kr} 분야별 종합 뉴스 브리핑\n\n"
    content += f"현재 시점을 기준으로 주요 분야의 최신 소식을 정리해 드립니다.\n\n"
    
    first_title = ""

    for category, url in feeds.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries: continue
            
            content += f"## {category}\n"
            for entry in feed.entries[:2]: # 각 분야별 상위 2개 핵심 뉴스
                title = entry.title
                summary = re.sub('<[^<]+?>', '', entry.description) if 'description' in entry else ""
                
                # 영어일 경우 한국어로 번역 (해외 매체 자동 감지)
                if any(x in url for x in ['technologyreview', 'nytimes']):
                    try:
                        title = translator.translate(title, dest='ko').text
                        summary = translator.translate(summary, dest='ko').text
                    except: pass # 번역 실패 시 원문 유지

                content += f"**{title}**: {summary[:300].strip()}...\n\n"
                
                if not first_title:
                    first_title = re.sub(r'[^가-힣a-zA-Z0-9\s]', '', title).strip()[:20]
        except Exception as e:
            print(f"Error: {e}")

    content += f"\n---\n### 📂 기록 안내\n본 내용은 GitHub Actions를 통해 자동 생성되어 Obsidian으로 동기화됩니다."
    
    filename = f"{today_str}_{first_title.replace(' ', '_')}.md"
    return filename, content

if __name__ == "__main__":
    filename, content = fetch_and_translate()
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
