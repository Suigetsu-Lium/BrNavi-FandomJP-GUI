# Backrooms Navigation System - Fandom JP


Backrooms Fandom JP のデータをローカルに構築し、Level検索・接続ネットワークのルート解析・テキストや画像のオフライン閲覧を行うナビゲーションシステムです。

## 📥 ダウンロードと起動方法
インストール不要のポータブルアプリです。※v1.0.1-hotfix 以降の最新版をご利用ください。

1. [GitHub Releases](https://github.com) から最新のZIPファイルをダウンロードします。
2. ZIPを解凍する前にファイルを右クリック ➔ **「プロパティ」** を開きます。
3. 全般タブ最下部の **「許可する」または「ブロックの解除」** にチェックを入れて「OK」を押します。
4. ZIPを解凍し、フォルダ内の `BackroomsNavigationSystem_FandomJP.exe` を実行してください。

## 🚀 初回データ構築の手順
保守タブの「Wikiを検索・閲覧・保存」はオンライン接続で利用でき、Master Databaseの構築は不要です。図鑑・ルート・分類機能も利用する場合は、データを同梱していないため、起動後に以下の順で既存データを構築してください。

```text
 [9] Master Database を更新
  ↓
 [5] Fandom JPからHTMLをダウンロード
  ↓
 [7] 情報データベースを更新 (ローカルHTML抽出)
  ↓
 [8] ネットワークを更新（1万件超のルートデータを自動解析）
  ↓
 [3] ルートを検索（セットアップ完了！すべての機能が解放されます）
```

## 🛠️ 主な機能
- **超高速ルート検索 (`[3]`, `[8]`)**：1万件以上のエッジから目的地までの最短経路を瞬時に探索。
- **インテリジェント検索・閲覧 (`[1]`, `[2]`, `[4]`)**：キーワードによるLevel検索とテキスト閲覧。
- **画像抽出・オフラインビューアー (`[5]`, `[6]`)**：HTMLから画像のみを抽出し、ローカルで表示。
- **GUI版Wiki閲覧・保存**：独立したWiki閲覧タブを開くと日本語版Backrooms Wikiのトップページを表示し、ページ内リンクをたどって自由に閲覧できます。ページのオンライン検索やタグ絞り込み、URLの直接入力にも対応し、Fandom本来のレイアウトで表示します。WindowsではChromiumベースのWebView2を使用します。「ローカルに保存」で表示中のWiki記事をHTMLと取得可能な画像とともに保存します。ダウンロードの詳細ログはCLIに表示し、GUIには保守処理の状態だけを表示します。Wikiの閲覧・保存はMaster Databaseに依存しません。既存の図鑑・ルート・分類機能は互換性維持のため引き続き既存データベースを使用します。
- **BrNavi AI Assistant**：Groq APIとローカルWiki資料を利用したMarkdownチャット。会話はローカルSQLiteに保存されます。Wiki調査では日本語版のローカル資料を優先し、見つからない場合はFandom APIで日本語版を検索して実際のページ本文を取得します。日本語版で該当情報を確認できた場合は外部検索を行わず、確認できない場合に英語版Wikiを検索します。検索にはインターネット接続が必要で、検索結果のスニペットだけを根拠には使用しません。

## 🧑‍💻 ソースからの起動
Python 3.10以降を用意し、リポジトリのルートで依存パッケージをインストールします。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python gui_app.py
```

GUI版は `gui_app.py`、コンソール版は `BackroomsNavigationSystem_FandomJP.py` から起動します。WindowsでGUI版のオンライン閲覧を利用するにはMicrosoft Edge WebView2 Runtimeが必要です（Chromiumを別途同梱する方式ではなく、WebView2 Runtimeを利用します）。AI Assistantを利用するには、GroqのAPIキーを `GROQ_API_KEY` 環境変数に設定してください。キーが未設定の場合、AI機能は利用できません。

### AIモジュールのimportエラー
`No module named 'bleach'` など依存パッケージが見つからない場合は、アプリの起動に使うPython環境と同じ環境で、リポジトリのルートから依存関係をインストールしてください。

```powershell
python -m pip install -r requirements.txt
python gui_app.py
```

仮想環境を使っている場合は、その環境を有効化してから実行してください。PyInstaller版を使っている場合、pipでの追加だけでは既存exeに反映されないため、依存をインストールした環境で再ビルドが必要です。

会話データと設定は `database/ai/` に保存されます。SQLiteスキーマの更新前にはSQLite Backup APIを使って同じフォルダにバックアップを作成します。アプリ内で会話を削除すると、関連するメッセージと回答バージョンも削除されます。

## 🧪 テスト
```powershell
python -m unittest discover -s tests -v
```

## ⚠️ 権利・ライセンスに関する免責事項
- **データの非同梱**：本ソフトにHTML、本文、画像データは同梱されていません。データは利用者が自身の責任で取得・管理してください。
- **ライセンスの遵守**：Fandom上のデータや画像には個別のCCライセンス等が存在します。ダウンロードしたデータの二次利用や再配布は、各Wikiの規約に従い利用者の責任で行ってください。
- **公式との関係**：本ソフトは個人が開発したファンツールであり、Backrooms Fandom JP公式、および各記事の作者・権利者とは一切関係ありません。
