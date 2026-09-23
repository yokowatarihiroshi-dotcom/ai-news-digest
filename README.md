# 🤖 AIニュースまとめ

毎朝自動でAI関連ニュースを収集・要約し、スマホで読みやすいWebページを生成するシステムです。

## 仕組み

1. GitHub Actions が毎朝7:00（日本時間）に自動実行
2. 10サイト以上のAIニュースRSSフィードを取得
3. AI活用事例・ビジネス応用に関する記事をフィルタリング
4. スマホに最適化されたHTMLページを自動生成
5. GitHub Pages で公開 → スマホのブラウザで閲覧

## 特徴

- 完全無料（GitHub無料プランの範囲内）
- 外部AI API不使用（RSSフィード + Pythonライブラリのみ）
- モバイルファーストデザイン（ダークモード対応）
- カテゴリフィルター、既読管理
- 過去7日分のアーカイブ

## ファイル構成

```
├── .github/workflows/
│   └── daily_news.yml      ← 毎朝の自動実行設定
├── scripts/
│   ├── fetch_news.py        ← メインスクリプト
│   ├── sources.yml          ← ニュースソース設定（編集可能）
│   └── requirements.txt     ← Pythonライブラリ
├── templates/
│   └── template.html        ← HTMLデザインテンプレート
├── data/
│   └── archive.json         ← 記事アーカイブ（自動生成）
├── docs/
│   └── index.html           ← 公開ページ（自動生成）
└── setup_guide.md           ← セットアップ手順
```

## ニュースソースの追加・削除

`scripts/sources.yml` を編集してください。

```yaml
# 新しいソースを追加する例
- name: "サイト名"
  url: "https://example.com/feed"
  category: "活用事例"    # 活用事例 / ビジネス / ツール / 研究
  language: "ja"           # ja / en
  enabled: true            # false にすると無効化
```

## 手動で今すぐ更新する方法

1. GitHubのリポジトリページを開く
2. 「Actions」タブをクリック
3. 左メニューから「AIニュースまとめ 自動更新」を選択
4. 「Run workflow」ボタンをクリック
5. 数分後にページが更新される

## セットアップ

初回セットアップの手順は [setup_guide.md](setup_guide.md) を参照してください。
