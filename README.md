# News Reading Podcast

Notionの「News Reading（音読ニュース）」データベースに毎晩追加される英語ニュース記事を、
[edge-tts](https://github.com/rany2/edge-tts) で音声化し、Podcast（RSSフィード）として配信するための仕組みです。

## 仕組み

1. GitHub Actionsが毎日 日本時間 0:30 に自動実行される（`.github/workflows/daily-podcast.yml`）
2. `scripts/build_episode.py` がNotion APIで「日付がその日 かつ 音読済みチェックが未実施」の記事を取得
3. 記事の「英文全体」セクションを取り出し、edge-ttsで音声（mp3）に変換
4. `docs/episodes/` にmp3を保存し、`docs/feed.xml`（Podcast用RSS）を更新
5. 変更をコミット・pushすると、GitHub Pagesで自動的に公開される
6. 処理した記事はNotion側の「音読済み」にチェックが入り、二重配信を防止

## 配信URL

GitHub Pagesを有効化すると、以下のURLで配信されます。

```
https://<GitHubユーザー名>.github.io/<リポジトリ名>/feed.xml
```

このURLをApple Podcastsの「番組をURLで追加」に入力すると購読できます。

## 手動実行

GitHubリポジトリの「Actions」タブ → 「Daily Podcast Build」→「Run workflow」から、
スケジュールを待たずに手動で実行できます。

## 必要なシークレット

- `NOTION_TOKEN`: Notion Integration の Internal Integration Secret
