#!/usr/bin/env python3
"""
AIニュースまとめ - RSS自動収集・HTML生成スクリプト

毎朝GitHub Actionsで実行され、複数のAIニュースサイトからRSSフィードを取得し、
スマートフォンで読みやすいHTMLページを自動生成します。

外部有料AI APIは一切使用しません。
英語記事は完全無料のオープンライブラリ（deep-translator）を用いて日本語へ自動翻訳します。
"""

import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import yaml
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader

# deep-translator のインポート
try:
    from deep_translator import GoogleTranslator, MyMemoryTranslator
    HAS_TRANSLATOR = True
except ImportError:
    HAS_TRANSLATOR = False


# ===== 定数 =====
JST = timezone(timedelta(hours=9))
MAX_SUMMARY_LENGTH = 200   # 要約の最大文字数
ARCHIVE_DAYS = 7            # アーカイブ保持日数
MAX_ARTICLES_PER_SOURCE = 15  # ソースあたりの最大取得記事数
FETCH_INTERVAL = 1.0        # ソース間の待機時間（秒）
TRANSLATE_INTERVAL = 0.5    # 翻訳API間の待機時間（秒）


# =====================================================================
# ユーティリティ関数
# =====================================================================

def clean_html(html_text: str) -> str:
    """HTMLタグを除去してプレーンテキストにする"""
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup.find_all(["script", "style", "iframe", "img"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate_at_sentence(text: str, max_length: int = MAX_SUMMARY_LENGTH) -> str:
    """文の途中で切らずに、適切な長さに切り詰める"""
    if len(text) <= max_length:
        return text

    sentence_endings = re.compile(r"(?<=[。．！？\.\!\?])\s*")
    sentences = sentence_endings.split(text)

    result = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(result) + len(sentence) <= max_length:
            result += sentence
        else:
            break

    if not result:
        result = text[:max_length].rsplit("、", 1)[0]
        if len(result) < max_length * 0.5:
            result = text[:max_length]
        result += "…"

    return result


def parse_published_date(entry) -> datetime:
    """記事の公開日時をパースする"""
    for attr in ["published_parsed", "updated_parsed", "created_parsed"]:
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                return dt.astimezone(JST)
            except (ValueError, TypeError):
                pass
    return datetime.now(JST)


def generate_article_id(link: str, title: str) -> str:
    """記事のユニークIDを生成"""
    key = f"{link or ''}{title or ''}".encode("utf-8")
    return hashlib.md5(key).hexdigest()[:12]


def matches_keywords(title: str, summary: str, keywords: list) -> bool:
    """タイトルまたは要約にキーワードが含まれるか判定"""
    if not keywords:
        return True
    text = f"{title} {summary}".lower()
    return any(kw.lower() in text for kw in keywords)


# =====================================================================
# 翻訳機能（二重フォールバック対応）
# =====================================================================

def contains_japanese(text: str) -> bool:
    """日本語文字（ひらがな・カタカナ・漢字）が含まれているか判定"""
    if not text:
        return False
    return bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', text))


def translate_text(text: str) -> str:
    """英語テキストを日本語に翻訳する（Google → MyMemoryの多重フォールバック）"""
    if not text or not text.strip():
        return ""
    if contains_japanese(text):
        return text

    # 1. GoogleTranslator を試行
    if HAS_TRANSLATOR:
        try:
            translated = GoogleTranslator(source='auto', target='ja').translate(text)
            time.sleep(TRANSLATE_INTERVAL)
            if translated and contains_japanese(translated):
                return translated
        except Exception as e:
            print(f"    Google翻訳試行エラー: {e}")

    # 2. MyMemoryTranslator を試行（フォールバック）
    if HAS_TRANSLATOR:
        try:
            translated = MyMemoryTranslator(source='en-US', target='ja-JP').translate(text)
            time.sleep(TRANSLATE_INTERVAL)
            if translated and contains_japanese(translated):
                return translated
        except Exception as e:
            print(f"    MyMemory翻訳試行エラー: {e}")

    # 3. 失敗した場合は原文を返す
    return text


def translate_articles(articles: list) -> list:
    """英語記事を日本語に翻訳する"""
    translated_count = 0
    # 英語記事で、かつタイトルに日本語が含まれていないものを対象とする
    targets = [
        a for a in articles
        if (a.get("language") == "en" and not contains_japanese(a.get("title", "")))
    ]

    if not targets:
        print("  未翻訳の英語記事はありません")
        return articles

    print(f"  英語記事を翻訳中 (対象: {len(targets)}件)...")

    for article in targets:
        original_title = article.get("title_original") or article.get("title", "")
        original_summary = article.get("summary_original") or article.get("summary", "")

        ja_title = translate_text(original_title)
        ja_summary = translate_text(original_summary)
        ja_summary = truncate_at_sentence(ja_summary)

        article["title_original"] = original_title
        article["summary_original"] = original_summary

        # 実際に日本語が含まれている場合のみ翻訳済みとして登録
        if contains_japanese(ja_title):
            article["title"] = ja_title
            article["is_translated"] = True
            translated_count += 1
        else:
            article["title"] = original_title
            article["is_translated"] = False

        if contains_japanese(ja_summary):
            article["summary"] = ja_summary
        else:
            article["summary"] = original_summary

    print(f"  → {translated_count}件 の英語記事を日本語に翻訳完了")
    return articles


# =====================================================================
# RSS取得
# =====================================================================

def fetch_single_feed(source: dict, keywords: list) -> list:
    """1つのRSSソースから記事を取得"""
    articles = []
    name = source["name"]
    url = source["url"]
    language = source.get("language", "ja")

    print(f"  取得中: {name}")

    try:
        feed = feedparser.parse(url, agent="AINewsDigest/1.0 (+https://github.com)")

        if feed.bozo and not feed.entries:
            print(f"    警告: フィードの取得またはパースに失敗しました")
            return []

        for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()

            if not title:
                continue

            raw_summary = entry.get("summary", "") or entry.get("description", "")
            summary = clean_html(raw_summary)
            summary = truncate_at_sentence(summary)

            if not matches_keywords(title, summary, keywords):
                continue

            published = parse_published_date(entry)

            articles.append({
                "id": generate_article_id(link, title),
                "title": title,
                "link": link,
                "summary": summary,
                "source": name,
                "category": source.get("category", "その他"),
                "language": language,
                "is_translated": False,
                "published": published.isoformat(),
                "published_date": published.strftime("%Y-%m-%d"),
            })

        print(f"    → {len(articles)}件 取得")

    except Exception as e:
        print(f"    エラー: {e}")

    return articles


def fetch_all_feeds(sources_config: dict) -> list:
    """全ソースからRSSフィードを取得"""
    all_articles = []
    sources = sources_config.get("sources", [])
    keywords_config = sources_config.get("keywords", {})

    for source in sources:
        if not source.get("enabled", True):
            print(f"  スキップ（無効）: {source['name']}")
            continue

        language = source.get("language", "ja")
        keywords = keywords_config.get(language, [])

        articles = fetch_single_feed(source, keywords)
        all_articles.extend(articles)

        time.sleep(FETCH_INTERVAL)

    return all_articles


# =====================================================================
# 重複排除
# =====================================================================

def deduplicate(articles: list) -> list:
    """URLとタイトルの重複を排除"""
    seen_links = set()
    seen_titles = set()
    unique = []

    for article in articles:
        link = article.get("link", "")
        title = article.get("title_original") or article.get("title", "")

        if link and link in seen_links:
            continue

        title_normalized = re.sub(r"\s+", "", title.lower())
        if title_normalized in seen_titles:
            continue

        seen_links.add(link)
        seen_titles.add(title_normalized)
        unique.append(article)

    removed = len(articles) - len(unique)
    if removed > 0:
        print(f"  重複排除: {removed}件を除外")

    return unique


# =====================================================================
# アーカイブ管理
# =====================================================================

def load_archive(archive_path: str) -> list:
    """過去のアーカイブデータを読み込む"""
    if os.path.exists(archive_path):
        try:
            with open(archive_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except (json.JSONDecodeError, IOError) as e:
            print(f"  アーカイブ読み込み警告: {e}")
    return []


def save_archive(archive_path: str, articles: list):
    """アーカイブデータを保存する"""
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)


def merge_and_prune(new_articles: list, archived: list, days: int = ARCHIVE_DAYS) -> list:
    """新しい記事をアーカイブとマージし、古い記事を削除"""
    existing_map = {a["id"]: a for a in archived}

    added = 0
    for article in new_articles:
        if article["id"] not in existing_map:
            archived.append(article)
            existing_map[article["id"]] = article
            added += 1

    cutoff = (datetime.now(JST) - timedelta(days=days)).strftime("%Y-%m-%d")
    before_count = len(archived)
    archived = [a for a in archived if a.get("published_date", "") >= cutoff]
    pruned = before_count - len(archived)

    archived.sort(key=lambda a: a.get("published", ""), reverse=True)

    print(f"  新規追加: {added}件 / 期限切れ削除: {pruned}件")
    return archived


# =====================================================================
# HTML生成
# =====================================================================

def group_by_date(articles: list) -> list:
    """記事を日付ごとにグループ化し、ラベルを付ける"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    yesterday = (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d")

    groups = {}
    for article in articles:
        date = article.get("published_date", today)
        if date not in groups:
            groups[date] = []
        groups[date].append(article)

    result = []
    for date in sorted(groups.keys(), reverse=True):
        if date == today:
            label = "今日"
        elif date == yesterday:
            label = "昨日"
        else:
            try:
                dt = datetime.strptime(date, "%Y-%m-%d")
                label = f"{dt.month}/{dt.day}"
            except ValueError:
                label = date

        result.append({
            "date": date,
            "label": label,
            "articles": groups[date],
        })

    return result


def get_unique_categories(articles: list) -> list:
    """記事からユニークなカテゴリ一覧を取得"""
    categories = []
    seen = set()
    for article in articles:
        cat = article.get("category", "その他")
        if cat not in seen:
            categories.append(cat)
            seen.add(cat)
    return sorted(categories)


def render_html(articles: list, template_dir: str, output_path: str):
    """Jinja2テンプレートでHTMLを生成"""
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=True,
    )
    template = env.get_template("template.html")

    date_groups = group_by_date(articles)
    categories = get_unique_categories(articles)

    now_jst = datetime.now(JST)
    generated_at = f"{now_jst.year}年{now_jst.month}月{now_jst.day}日 {now_jst.strftime('%H:%M')}"

    html = template.render(
        generated_at=generated_at,
        date_groups=date_groups,
        categories=categories,
        total_count=len(articles),
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  出力: {output_path}")


# =====================================================================
# メイン
# =====================================================================

def main():
    print("=" * 50)
    print("AIニュースまとめ - 自動生成")
    print("=" * 50)

    base_dir = Path(__file__).resolve().parent.parent
    sources_path = base_dir / "scripts" / "sources.yml"
    archive_path = base_dir / "data" / "archive.json"
    template_dir = str(base_dir / "templates")
    output_path = str(base_dir / "docs" / "index.html")

    # 1. ソース設定を読み込み
    print("\n[1/6] ソース設定を読み込み中...")
    with open(sources_path, "r", encoding="utf-8") as f:
        sources_config = yaml.safe_load(f)

    enabled_count = sum(
        1 for s in sources_config.get("sources", [])
        if s.get("enabled", True)
    )
    print(f"  有効なソース: {enabled_count}件")

    # 2. RSSフィードを取得
    print("\n[2/6] RSSフィードを取得中...")
    new_articles = fetch_all_feeds(sources_config)
    print(f"  取得合計: {len(new_articles)}件")

    # 3. 重複排除
    print("\n[3/6] 重複チェック...")
    new_articles = deduplicate(new_articles)
    print(f"  重複排除後: {len(new_articles)}件")

    # 4. アーカイブとマージ
    print("\n[4/6] アーカイブ処理...")
    archived = load_archive(str(archive_path))
    print(f"  既存アーカイブ: {len(archived)}件")
    all_articles = merge_and_prune(new_articles, archived)

    # 5. 英語記事の日本語翻訳（未翻訳のものを対象に翻訳）
    print("\n[5/6] 翻訳処理...")
    all_articles = translate_articles(all_articles)
    save_archive(str(archive_path), all_articles)
    print(f"  最終記事数: {len(all_articles)}件")

    # 6. HTML生成
    print("\n[6/6] HTMLページを生成中...")
    render_html(all_articles, template_dir, output_path)

    print("\n" + "=" * 50)
    print(f"完了: {len(all_articles)}件の記事をHTMLに出力しました")
    print("=" * 50)


if __name__ == "__main__":
    main()
