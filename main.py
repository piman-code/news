import feedparser
import datetime
import re
from newspaper import Article

def get_article_content(url):
    """
    기사 URL을 타고 들어가 본문을 추출하고, 
    앞부분(핵심 리드문)을 약 300~400자 정도로 잘라서 반환합니다.
    """
    try:
        article = Article(url, language='ko')
        article.download()
        article.parse()
        
        # 본문 가져오기 (없으면 공란)
        text = article.text.strip()
        
        if len(text) < 50: # 본문 추출 실패 시
            return ""

        # 가독성을 위해 문단 정리 (줄바꿈 과다 제거)
        text = re.sub(r'\n+', ' ', text)
        
        # 핵심 내용인 앞부분 350자 추출 (문장 중간에 끊기지 않게 마침표 처리)
        summary = text[:350]
        if "." in summary[300:]: # 300자 이후 첫 마침표에서 끊기
            summary = summary[:300] + summary[300:].split('.')[0] + "."
        else:
            summary += "..."
            
        return summary
    except Exception as e:
        return ""

def fetch_korean_news():
    # 1. 국내 권위 있는 뉴스 소스
    sources = {
        "🤖 인공지능 (AI)": "http://www.aitimes.com/rss/allArticle.xml", 
        "🏛️ 정치": "https://www.yna.co.kr/rss/politics.xml", 
        "🏥 사회": "https://www.yna.co.kr/rss/society.xml",
        # [수정됨] 한국교육신문 -> 연합뉴스 교육 섹션 (스크랩 안정성 확보)
        "🎓 교육": "https://www.yna.co.kr/rss/society-education.xml" 
    }
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    today_kr = now.strftime("%Y년 %m월 %d일(%a)")
    
    # 2. 마크다운 헤더 작성
    markdown = f"---\ndate: {today_str}\ntags: [뉴스, 스크랩, {today_str}]\n---\n\n"
    markdown += f"# 📅 {today_kr} 분야별 핵심 뉴스 브리핑\n\n"
    markdown += f"국내 주요 언론사의 기사 원문을 바탕으로 정리된 최신 뉴스입니다. 제목을 클릭하면 원문을 확인하실 수 있습니다.\n\n"
    
    first_title = "" 

    for category, rss_url in sources.items():
        markdown += f"## {category}\n"
        
        try:
            feed = feedparser.parse(rss_url)
            # 분야별 최신 기사 2~3개 선정
            count = 0
            for entry in feed.entries:
                if count >= 2: break # 분야별 2개만 (너무 길어짐 방지)
                
                # (1) 본문 내용 가져오기
                content_summary = get_article_content(entry.link)
                
                # 본문 추출에 실패했으면 RSS 기본 설명 사용
                if not content_summary:
                    if 'description' in entry:
                        content_summary = re.sub('<[^<]+?>', '', entry.description)[:200] + "..."
                    else:
                        continue # 내용이 아예 없으면 건너뜀

                # (2) 출력 포맷: [제목](링크) + 내용
                markdown += f"### 🔗 [{entry.title}]({entry.link})\n"
                markdown += f"> {content_summary}\n\n"

                # 파일명 생성용 (첫 기사 제목)
                if not first_title:
                    first_title = re.sub(r'[^가-힣a-zA-Z0-9]', '', entry.title)[:15]
                
                count += 1

        except Exception as e:
            print(f"Error processing {category}: {e}")
            markdown += "뉴스 수집 중 일시적인 오류가 발생했습니다.\n\n"

    # 3. 푸터 작성
    markdown += "---\n"
    markdown += "### 📂 자동화 기록 안내\n"
    markdown += f"위 내용은 GitHub Actions를 통해 국내 언론사 RSS에서 실시간으로 수집되었습니다.\n"
    
    return f"{today_str}_{first_title}.md", markdown

if __name__ == "__main__":
    filename, content = fetch_korean_news()
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"File created: {filename}")
