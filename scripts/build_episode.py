"""
Notionの「News Reading」データベースから当日分の記事を取得し、
英文本文をedge-ttsで音声化してPodcastのfeed.xmlを更新する。
GitHub Actions上で毎晩実行される想定。
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from mutagen.mp3 import MP3

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATA_SOURCE_ID = "5fcfcfce-8ade-401a-aecd-49a916637fdb"
NOTION_VERSION = "2025-09-03"
VOICE = "en-US-AriaNeural"

BASE_URL = os.environ.get("PAGES_BASE_URL", "").rstrip("/")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")  # "owner/repo"
JSDELIVR_BRANCH = "main"

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
EPISODES_DIR = DOCS_DIR / "episodes"
EPISODES_JSON = DOCS_DIR / "episodes.json"
FEED_PATH = DOCS_DIR / "feed.xml"
COVER_URL = f"{BASE_URL}/cover.jpg"

# GitHub Pagesはmp3にContent-Type: audio/mp3を返しApple Podcastsに拒否されるため、
# 音声ファイルだけは正しいContent-Type(audio/mpeg)で配信されるjsDelivr CDN経由にする。
AUDIO_BASE_URL = f"https://cdn.jsdelivr.net/gh/{GITHUB_REPOSITORY}@{JSDELIVR_BRANCH}/docs/episodes"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

JST = timezone(timedelta(hours=9))

CHANNEL_TITLE = "News Reading（音読ニュース）"
CHANNEL_DESC = "Notionに毎晩追加される英語ニュース記事を、英語音声のまま聴けるPodcastです。"


def today_jst_str():
    return datetime.now(JST).strftime("%Y-%m-%d")


def query_today_articles(date_str):
    url = f"https://api.notion.com/v1/data_sources/{DATA_SOURCE_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "日付", "date": {"equals": date_str}},
                {"property": "音読済み", "checkbox": {"equals": False}},
            ]
        }
    }
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("results", [])


def get_page_title(page):
    props = page["properties"]
    title_prop = props.get("Name", {}).get("title", [])
    if title_prop:
        return "".join(t.get("plain_text", "") for t in title_prop)
    return "News Reading"


def get_english_body(page_id):
    """ページ本文から「## 英文全体」見出しの直後の段落を英語本文として取り出す"""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    blocks = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        blocks.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    capturing = False
    texts = []
    for block in blocks:
        btype = block.get("type")
        if btype in ("heading_1", "heading_2", "heading_3"):
            heading_text = "".join(
                t.get("plain_text", "") for t in block[btype].get("rich_text", [])
            ).strip()
            if heading_text == "英文全体":
                capturing = True
                continue
            if capturing:
                break  # 次の見出しに到達したら終了
        elif capturing and btype == "paragraph":
            para_text = "".join(
                t.get("plain_text", "") for t in block["paragraph"].get("rich_text", [])
            )
            if para_text.strip():
                texts.append(para_text.strip())

    return " ".join(texts).strip()


def mark_as_read(page_id):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {"properties": {"音読済み": {"checkbox": True}}}
    resp = requests.patch(url, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()


def synthesize_speech(text, out_path):
    text_file = out_path.with_suffix(".txt")
    text_file.write_text(text, encoding="utf-8")
    try:
        subprocess.run(
            [
                "edge-tts",
                "--voice", VOICE,
                "--file", str(text_file),
                "--write-media", str(out_path),
            ],
            check=True,
        )
    finally:
        text_file.unlink(missing_ok=True)


def load_episodes():
    if not EPISODES_JSON.exists():
        return []
    episodes = json.loads(EPISODES_JSON.read_text(encoding="utf-8"))
    for ep in episodes:
        if "filename" not in ep:
            ep["filename"] = ep["audio_url"].rsplit("/", 1)[-1]
        ep.pop("audio_url", None)
    return episodes


def purge_jsdelivr_cache(filename):
    if not GITHUB_REPOSITORY:
        return
    url = f"https://purge.jsdelivr.net/gh/{GITHUB_REPOSITORY}@{JSDELIVR_BRANCH}/docs/episodes/{filename}"
    try:
        requests.get(url, timeout=15)
    except requests.RequestException as e:
        print(f"jsDelivrキャッシュの更新リクエストに失敗しました(無視して続行): {e}")


def save_episodes(episodes):
    EPISODES_JSON.write_text(
        json.dumps(episodes, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_feed(episodes):
    episodes_sorted = sorted(episodes, key=lambda e: e["pub_date_raw"], reverse=True)
    items_xml = []
    for ep in episodes_sorted:
        audio_url = f"{AUDIO_BASE_URL}/{ep['filename']}"
        items_xml.append(f"""
    <item>
      <title>{escape(ep['title'])}</title>
      <description>{escape(ep['description'])}</description>
      <pubDate>{ep['pub_date_rfc822']}</pubDate>
      <enclosure url="{escape(audio_url)}" length="{ep['file_size']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{ep['guid']}</guid>
      <itunes:duration>{ep['duration']}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""")

    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(CHANNEL_TITLE)}</title>
    <link>{BASE_URL}/</link>
    <language>ja</language>
    <itunes:author>News Reading</itunes:author>
    <description>{escape(CHANNEL_DESC)}</description>
    <itunes:summary>{escape(CHANNEL_DESC)}</itunes:summary>
    <itunes:image href="{COVER_URL}"/>
    <image>
      <url>{COVER_URL}</url>
      <title>{escape(CHANNEL_TITLE)}</title>
      <link>{BASE_URL}/</link>
    </image>
    <itunes:category text="Education"/>
    <itunes:explicit>false</itunes:explicit>
    <atom:link href="{BASE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
{''.join(items_xml)}
  </channel>
</rss>
"""
    FEED_PATH.write_text(feed, encoding="utf-8")


def main():
    if not BASE_URL:
        print("PAGES_BASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    date_str = today_jst_str()
    articles = query_today_articles(date_str)

    if not articles:
        print(f"{date_str} の記事が見つかりませんでした。処理をスキップします。")
        return

    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    episodes = load_episodes()
    existing_guids = {e["guid"] for e in episodes}

    for page in articles:
        page_id = page["id"]
        if page_id in existing_guids:
            continue

        title = get_page_title(page)
        body = get_english_body(page_id)
        if not body:
            print(f"本文が見つかりませんでした: {title}")
            continue

        short_id = page_id.replace("-", "")[:8]
        filename = f"{date_str}-{short_id}.mp3"
        out_path = EPISODES_DIR / filename

        print(f"音声を生成中: {title}")
        synthesize_speech(body, out_path)

        audio = MP3(str(out_path))
        duration_seconds = int(audio.info.length)
        h, rem = divmod(duration_seconds, 3600)
        m, s = divmod(rem, 60)
        duration_str = f"{h:02d}:{m:02d}:{s:02d}"

        pub_dt = datetime.now(JST)
        episodes.append({
            "guid": page_id,
            "title": title,
            "description": body,
            "filename": filename,
            "file_size": out_path.stat().st_size,
            "duration": duration_str,
            "pub_date_raw": pub_dt.isoformat(),
            "pub_date_rfc822": pub_dt.strftime("%a, %d %b %Y %H:%M:%S %z"),
        })

        purge_jsdelivr_cache(filename)
        mark_as_read(page_id)
        print(f"完了: {title}")

    save_episodes(episodes)
    build_feed(episodes)
    print("すべての処理が完了しました。")


if __name__ == "__main__":
    main()
