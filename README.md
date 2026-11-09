# BrNavi Fandom JP

Backrooms日本語版Fandom Wikiを閲覧するためのPython製デスクトップアプリです。ローカルのWikiデータを使ったLevel検索・閲覧、Fandomの埋め込み閲覧・保存、Groq APIを使った出典付きAIチャットを利用できます。

## 機能

### Level図鑑・検索

- ローカルの抽出済みWiki本文からLevelを検索・閲覧します。
- Level番号は完全一致で検索し、カテゴリの優先順に一覧表示します。
- Levelの分類、危険度、理解度、画像などを表示します。
- Master Databaseは一覧のカテゴリ・言語などの補助情報に利用します。
- 既存のローカルHTMLやFandomページを開く機能もあります。

このタブの利用には、対象のWikiデータがローカルに必要です。データがない場合、検索結果や本文、画像を表示できません。

### Wiki閲覧・保存

- アプリ内のWikiタブにFandom日本語版を埋め込んで表示します。最初にタブを開くと、[Backrooms Wiki](https://backrooms.fandom.com/ja/wiki/Backrooms_Wiki)を読み込みます。
- 埋め込みブラウザー内のリンクをたどって自由に閲覧できます。URL欄から日本語版Wikiの記事へ直接移動することもできます。
- Fandom検索と、検索結果のカテゴリタグによる絞り込みに対応します。検索結果を選ぶと埋め込み表示先がその記事に変わります。
- 「表示中を保存」は、現在表示中の日本語版Wiki記事をHTMLとして保存し、取得可能な画像を記事ごとのアセットフォルダーへ保存します。
- Wiki検索・閲覧・保存はMaster Databaseを必要としません。ダウンロードの詳細ログはCLIに出力し、GUIには保存状況を表示します。

保存できるURLは `https://backrooms.fandom.com/ja/wiki/` 配下の記事です。Fandom側の仕様やネットワーク、広告・動的コンテンツなどにより、ページ表示や画像保存の内容が変わる場合があります。保存されるHTMLはオフライン閲覧用であり、Fandomページの完全な複製を保証するものではありません。

### データ保守

データ保守タブには、既存データとの互換性を保つための処理があります。

- **既存データベース更新**：Master Databaseを更新します。
- **情報データベース更新**：保存済みHTMLから本文・タグなどを抽出します。
- **ネットワーク更新**：抽出済みデータからLevel間の接続情報を生成します。

GUIにはタスクの開始・完了・失敗だけを表示します。詳細な処理ログはCLI側で確認してください。これらのローカルデータ処理は、Wikiタブでのオンライン閲覧・保存には不要です。

### BrNavi AI Assistant

- Groq APIを利用する日本語Markdownチャットです。
- ローカルのWiki資料を優先して検索し、必要に応じてFandom日本語版を検索・取得します。日本語版で根拠を確認できない場合は、英語版Wikiなどの外部資料を探索します。
- 検索結果のスニペットだけを根拠にせず、取得できたページ本文を使って回答を検証します。回答では日本語Wiki、外部情報、考察・推測、未確認情報を区別し、参照資料や検索上の注意を表示します。
- Markdown表示、コードブロックのコピー、回答のコピー、Good / Bad評価に対応します。
- 回答の再生成結果は過去の回答を残したままバージョンとして保存でき、前後の版を切り替えられます。生成中は停止できます。
- 会話の新規作成・名称変更・削除に対応します。タイトルは自動生成されますが、手動で名称を変更した会話は自動生成で上書きしません。

AIを使うには `GROQ_API_KEY` 環境変数が必要です。未設定または無効の場合、AI機能は利用できません。Groq APIの利用にはインターネット接続とGroq側の利用条件が適用されます。

会話、回答バージョン、評価、設定は `database/ai/` に保存されます。SQLiteのスキーマ更新時は、既存データベースがある場合にSQLite Backup APIでバックアップを作成してからマイグレーションします。会話を削除すると、関連メッセージと回答バージョンも削除されます。

## 起動方法

### GUIアプリ

Python 3.10以降を用意し、リポジトリのルートで次を実行します。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python gui_app.py
```

WindowsではGUIの表示にMicrosoft Edge WebView2 Runtimeを使用します。Chromiumをアプリに同梱する方式ではありません。WebView2 Runtimeがない場合は、MicrosoftのWebView2 Runtimeをインストールしてください。

AI Assistantを使う場合は、同じPowerShellセッションでAPIキーを設定してから起動します。

```powershell
$env:GROQ_API_KEY = "your-groq-api-key"
python gui_app.py
```

APIキーをソースコードやREADMEに書き込まないでください。

### コンソール版

従来の対話型コンソールアプリは、リポジトリのルートから起動できます。

```powershell
python BackroomsNavigationSystem_FandomJP.py
```

Level検索・情報表示・ルート検索・ローカルデータ閲覧のほか、HTML・画像のダウンロードやデータベース更新メニューがあります。コンソール版のHTMLダウンロードはMaster Databaseを使う従来方式で、GUIのオンラインWikiタブとは別の機能です。

### 配布用ビルド

`build_release.bat` はPyInstallerでコンソール版 `BackroomsNavigationSystem_FandomJP.py` をビルドします。現在のスクリプトはGUI版 `gui_app.py` の配布用exeをビルドしません。ビルド時に存在する `parsed_index`、`level_lists`、`index_html` は配布フォルダーへコピーされます。

## 主なデータフォルダー

| パス | 用途 |
| --- | --- |
| `parsed_index/` | Master Databaseなどのインデックスデータ |
| `downloaded_fandom_html/` | 保存したWiki HTMLと画像アセット |
| `extracted_fandom_levels/` | HTMLから抽出したLevel本文。図鑑・AI検索で利用 |
| `downloaded_images/` | Level図鑑用の画像 |
| `database/ai/` | AI AssistantのSQLite会話履歴、マイグレーションバックアップ、設定 |

これらのデータは常にリポジトリへ同梱されるわけではありません。Wikiのオンライン閲覧・保存以外の機能は、必要なローカルデータが用意されていることを確認してください。

## テスト

依存パッケージをインストール後、リポジトリのルートから実行します。

```powershell
python -m unittest discover -s tests -v
```

## ライセンスと注意事項

このソフトウェアはMIT Licenseです（[`LICENSE`](LICENSE)を参照）。Fandom上の記事・画像等の著作物はこのソフトウェアのライセンス対象ではありません。保存データの利用・再配布にあたっては、各作品・ページのライセンス、Fandomの利用規約、および権利者の条件を確認してください。本ソフトウェアはBackrooms Fandom JP公式とは関係ありません。
