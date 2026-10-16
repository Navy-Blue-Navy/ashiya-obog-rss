import os
import re
import time
import hashlib
import requests
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from urllib.parse import urljoin


# ============================================================
# 設定
# ============================================================

BASE_URL = "https://ashi-rug.info/"
CATEGORY_URL = "https://ashi-rug.info/?cat=20"

OUTPUT_FILE = "ashiya_obog.xml"

FEED_TITLE = "芦屋ラグビースクール OB&OG"
FEED_LINK = CATEGORY_URL
FEED_DESCRIPTION = "芦屋ラグビースクール OB&OGカテゴリーの更新情報"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


# ============================================================
# HTML取得
# ============================================================

def fetch_page(url, retries=3):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            print(f"取得 {attempt}/{retries}: {url}")

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )

            print(f"HTTP: {response.status_code}")

            response.raise_for_status()

            response.encoding = response.apparent_encoding or "utf-8"

            print(f"HTML文字数: {len(response.text)}")

            return response.text

        except Exception as e:
            last_error = e
            print(f"取得失敗: {repr(e)}")

            if attempt < retries:
                print("10秒待って再試行します")
                time.sleep(10)

    raise RuntimeError(
        f"{retries}回とも取得に失敗しました: {last_error}"
    )


# ============================================================
# 日付
# ============================================================

def parse_date(text):
    if not text:
        return None

    text = text.strip()

    match = re.search(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
        text,
    )

    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))

    return datetime(
        year,
        month,
        day,
        0,
        0,
        0,
        tzinfo=JST,
    )


# ============================================================
# 1ページの記事解析
# ============================================================

def parse_articles(html):
    soup = BeautifulSoup(html, "html.parser")

    articles = []
    seen = set()

    for title_tag in soup.select("h1.entry-title"):
        link_tag = title_tag.find("a", href=True)

        if not link_tag:
            continue

        title = link_tag.get_text(" ", strip=True)
        link = urljoin(BASE_URL, link_tag["href"])

        if not title or not link:
            continue

        if link in seen:
            continue

        seen.add(link)

        container = title_tag.find_parent(
            class_=lambda x: x and "media-body" in x
        )

        if container is None:
            container = title_tag.parent

        date = None
        description = ""

        if container:
            date_tag = container.select_one(
                ".published.entry-meta_items"
            )

            if date_tag:
                date = parse_date(
                    date_tag.get_text(" ", strip=True)
                )

            excerpt_tag = container.select_one(
                ".media-body_excerpt"
            )

            if excerpt_tag:
                description = excerpt_tag.get_text(
                    " ",
                    strip=True,
                )

        if date is None:
            print(
                f"日付を取得できないため除外: {title}"
            )
            continue

        articles.append(
            {
                "title": title,
                "link": link,
                "date": date,
                "description": description,
            }
        )

    return articles


# ============================================================
# 最大ページ数取得
# ============================================================

def detect_max_page(html):
    soup = BeautifulSoup(html, "html.parser")

    pages = [1]

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")

        match = re.search(
            r"[?&]paged=(\d+)",
            href,
        )

        if match:
            pages.append(int(match.group(1)))

    return max(pages)


# ============================================================
# 全ページ取得
# ============================================================

def fetch_all_articles():
    first_html = fetch_page(CATEGORY_URL)

    max_page = detect_max_page(first_html)

    print(f"検出した最大ページ: {max_page}")

    all_articles = []
    seen_links = set()

    for page in range(1, max_page + 1):

        if page == 1:
            html = first_html
            url = CATEGORY_URL
        else:
            url = (
                f"{BASE_URL}?paged={page}&cat=20"
            )

            html = fetch_page(url)

        articles = parse_articles(html)

        print(
            f"ページ {page}: {len(articles)} 件"
        )

        if not articles:
            print(
                f"ページ {page} に記事がありません"
            )
            continue

        for article in articles:
            if article["link"] in seen_links:
                continue

            seen_links.add(article["link"])
            all_articles.append(article)

    all_articles.sort(
        key=lambda x: x["date"],
        reverse=True,
    )

    return all_articles


# ============================================================
# 既存RSS読み込み
# ============================================================

def load_existing_items():
    existing = {}

    if not os.path.exists(OUTPUT_FILE):
        return existing

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        for item in root.findall("./channel/item"):
            link = item.findtext("link", "").strip()

            if not link:
                continue

            title = item.findtext(
                "title",
                "",
            ).strip()

            description = item.findtext(
                "description",
                "",
            ).strip()

            pub_date_text = item.findtext(
                "pubDate",
                "",
            ).strip()

            guid = item.findtext(
                "guid",
                "",
            ).strip()

            existing[link] = {
                "title": title,
                "link": link,
                "description": description,
                "pubDate": pub_date_text,
                "guid": guid,
            }

    except Exception as e:
        print(
            f"既存RSSの読み込みに失敗: {repr(e)}"
        )

    return existing


# ============================================================
# GUID
# ============================================================

def make_guid(link):
    return hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()


# ============================================================
# RSS生成
# ============================================================

def build_rss(current_articles, existing_items):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
        },
    )

    channel = ET.SubElement(rss, "channel")

    ET.SubElement(
        channel,
        "title",
    ).text = FEED_TITLE

    ET.SubElement(
        channel,
        "link",
    ).text = FEED_LINK

    ET.SubElement(
        channel,
        "description",
    ).text = FEED_DESCRIPTION

    ET.SubElement(
        channel,
        "language",
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        datetime.now(JST)
    )

    merged = {}

    # --------------------------------------------------------
    # 既存RSS
    # --------------------------------------------------------

    for link, item in existing_items.items():
        merged[link] = {
            "title": item["title"],
            "link": link,
            "description": item["description"],
            "pubDate": item["pubDate"],
            "guid": item["guid"] or make_guid(link),
        }

    # --------------------------------------------------------
    # 今回取得分
    # --------------------------------------------------------

    for article in current_articles:
        link = article["link"]

        merged[link] = {
            "title": article["title"],
            "link": link,
            "description": article["description"],
            "pubDate": format_datetime(
                article["date"]
            ),
            "guid": make_guid(link),
        }

    # --------------------------------------------------------
    # 日付順
    # --------------------------------------------------------

    def sort_key(item):
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(
                item["pubDate"]
            )

            return dt.timestamp()

        except Exception:
            return 0

    sorted_items = sorted(
        merged.values(),
        key=sort_key,
        reverse=True,
    )

    # --------------------------------------------------------
    # XML item
    # --------------------------------------------------------

    for article in sorted_items:
        item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = article["title"]

        ET.SubElement(
            item,
            "link",
        ).text = article["link"]

        ET.SubElement(
            item,
            "description",
        ).text = article["description"]

        ET.SubElement(
            item,
            "pubDate",
        ).text = article["pubDate"]

        guid = ET.SubElement(
            item,
            "guid",
            {
                "isPermaLink": "false",
            },
        )

        guid.text = article["guid"]

    tree = ET.ElementTree(rss)

    ET.indent(
        tree,
        space="  ",
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    return len(sorted_items)


# ============================================================
# メイン
# ============================================================

def main():
    print("=" * 70)
    print("芦屋ラグビースクール OB&OG RSS")
    print("=" * 70)

    existing_items = load_existing_items()

    print(
        f"既存RSS件数: {len(existing_items)}"
    )

    try:
        current_articles = fetch_all_articles()

    except Exception as e:
        print()
        print(f"エラー: {repr(e)}")
        print(
            "RSSは更新せず、既存ファイルを保持します"
        )
        raise

    print()
    print(
        f"今回取得した記事数: {len(current_articles)}"
    )

    print()
    print("最新20件:")

    for i, article in enumerate(
        current_articles[:20],
        1,
    ):
        print(
            f"{i:2d}. "
            f"{article['date'].strftime('%Y-%m-%d')} "
            f"{article['title']}"
        )
        print(
            f"    {article['link']}"
        )

    existing_links = set(
        existing_items.keys()
    )

    new_articles = [
        article
        for article in current_articles
        if article["link"] not in existing_links
    ]

    print()
    print(
        f"新規記事数: {len(new_articles)}"
    )

    total = build_rss(
        current_articles,
        existing_items,
    )

    print(
        f"保存完了: {OUTPUT_FILE}"
    )

    print(
        f"RSS総件数: {total}"
    )


if __name__ == "__main__":
    main()