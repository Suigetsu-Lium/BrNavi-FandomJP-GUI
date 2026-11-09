# BrNavi Fandom JP

Backrooms日本語版Fandom Wikiを閲覧するためのPython製デスクトップアプリです。ローカルWikiデータを使ったLevel検索・閲覧、Fandomの埋め込み閲覧・保存、Groq APIを使った出典付きAIチャットを提供します。

> **配布:** Windows向けGUI版・CLI版は[GitHub Releases](https://github.com/Suigetsu-Lium/BrNavi-FandomJP-GUI/releases/latest)からダウンロードできます。ソースから `build_release.bat` を実行して個別にビルドすることもできます。

## 目次

- [機能](#機能)
- [ダウンロードと起動方法](#ダウンロードと起動方法)
- [制約とトラブルシューティング](#制約とトラブルシューティング)
- [ライセンスとコンテンツ利用](#ライセンスとコンテンツ利用)

## 機能

### Level図鑑・検索

- ローカルの抽出済みWiki本文からLevelを検索・閲覧します。
- Level番号は完全一致で検索します。検索対象の基本は本文ファイル名で、カテゴリ・言語などの補助情報はMaster Databaseから取得します。
- Levelの分類、危険度、理解度、画像などを表示します。
- 既存のローカルHTMLやFandomページを開く機能があります。

対象のWikiデータがローカルにない場合、このタブで検索結果、本文、画像を十分に表示できません。

### Wiki閲覧・保存

- Wikiタブ内にFandom日本語版を埋め込みます。最初にタブを開くと[Backrooms Wiki](https://backrooms.fandom.com/ja/wiki/Backrooms_Wiki)を表示します。
- ページ内リンクをたどって自由に閲覧できます。URL欄から記事URLを入力して移動することもできます。
- Fandom検索と検索結果のカテゴリタグによる絞り込みに対応します。
- 「表示中を保存」は表示中の記事HTMLを保存し、取得可能な画像を記事ごとのアセットフォルダーへ保存します。
- Wiki検索・閲覧・保存はMaster Databaseを必要としません。

保存できるURLは `https://backrooms.fandom.com/ja/wiki/` 配下の記事です。保存HTMLはオフライン閲覧用であり、Fandomページ全体（広告、ログイン状態、動的コンテンツ、スクリプト等）の完全な複製を保証しません。ダウンロードの詳細ログはCLIに出力し、GUIには保存状況を表示します。

### データ保守

保守タブには既存のローカルデータ機能があります。

| 操作 | 役割 |
| --- | --- |
| 既存データベース更新 | Master Databaseを更新 |
| 情報データベース更新 | 保存済みHTMLから本文・タグなどを抽出 |
| ネットワーク更新 | 抽出済みデータからLevel間の接続情報を生成 |

GUIはタスクの開始・完了・失敗を表示し、詳細な処理ログはCLI側へ出力します。これらのローカル処理はWikiタブのオンライン閲覧・保存には不要です。

### BrNavi AI Assistant

- Groq APIを利用する日本語Markdownチャットです。
- Markdownの見出し・リスト・引用・表・コード表示に対応します。コードブロックはPygmentsでハイライトし、表は横スクロールできます。表示前にHTMLをサニタイズします。
- 回答のコピー、Good / Bad評価、再生成、回答バージョンの前後切り替えに対応します。再生成では以前の回答を削除せず、生成に失敗した場合も以前の回答を維持します。
- 生成はストリーミング表示し、生成中は停止できます。会話の新規作成・名称変更・削除、会話タイトルの自動生成にも対応します。手動設定した会話名は自動生成で上書きしません。

AIを使用するには `GROQ_API_KEY` 環境変数が必要です。APIキーが未設定または無効の場合、AI機能は利用できません。

## ダウンロードと起動方法

### GUIアプリ（Windows）

GitHub ReleasesからGUI版のZIPをダウンロード・展開して `BrNavi-GUI.exe` を起動します。CLIを使う場合は `BrNavi-CLI.exe` を起動してください。各ZIPはonedir形式のため、exeだけでなくZIP内のファイル一式が必要です。ソースから実行する場合は、[リポジトリのソースZIP](https://github.com/Suigetsu-Lium/BrNavi-FandomJP-GUI/archive/refs/heads/main.zip)を展開し、Python 3.10以降をインストールして次を実行します。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python gui_app.py
```

Windows GUIはMicrosoft Edge WebView2 Runtimeを使用します。Chromiumをアプリに同梱する方式ではありません。Runtimeがない場合は[Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)をインストールしてください。

AI Assistantを使用する場合は、同じPowerShellセッションでAPIキーを設定してから起動します。

```powershell
$env:GROQ_API_KEY = "your-groq-api-key"
python gui_app.py
```

APIキーをソースコードやREADMEに記載したり、公開リポジトリへ登録したりしないでください。

### コンソールアプリ

従来の対話型コンソールアプリは次のコマンドで起動します。

```powershell
python BackroomsNavigationSystem_FandomJP.py
```

Level検索・情報表示・ルート検索・ローカルデータ閲覧、HTML・画像ダウンロード、データベース更新を行います。コンソール版HTMLダウンロードはMaster Databaseを使う従来方式であり、GUIのオンラインWikiタブとは別の機能です。

### 配布用ビルド

依存関係をインストールしたWindows環境で、リポジトリのルートから実行します。

```powershell
.\build_release.bat
```

`.venv` がある場合はそのPythonを使い、なければ `python` を使います。ビルド結果は次の2つのonedirフォルダーに分かれます。

| 種類 | 実行ファイル |
| --- | --- |
| GUI | `dist\BrNavi-GUI\BrNavi-GUI\BrNavi-GUI.exe` |
| CLI | `dist\BrNavi-CLI\BrNavi-CLI\BrNavi-CLI.exe` |

onedir形式ではexeだけでなく同じ出力フォルダー内のDLL・Python依存ファイルも必要です。配布時は各 `BrNavi-GUI` / `BrNavi-CLI` フォルダーを丸ごと渡してください。ビルド時に存在するローカルWikiデータも各出力へコピーし、不足データ用の空フォルダーを作成します。会話履歴などの `database\ai` はコピーせず、配布先で新規作成されます。README、アーキテクチャ資料、ライセンスも配布フォルダーに含まれます。出力先は書き込み可能な場所に置いてください。

GUI版はpywebviewをWindows Edge Chromiumバックエンドで起動します。WebView2 Runtimeはexeに含まれないため、配布先Windowsにも必要です。APIキーは同梱せず、利用者の `GROQ_API_KEY` 環境変数から読み込みます。

## 技術資料

内部構成、Wiki・AI・SQLiteのデータフロー、主要モジュール、開発・テスト方針は[アーキテクチャと開発ガイド](docs/architecture.md)を参照してください。

## 制約とトラブルシューティング

- **GUIのWebViewが起動しない:** WindowsにWebView2 Runtimeがインストールされているか確認してください。
- **Wikiが表示されない:** インターネット接続とFandomの応答を確認してください。埋め込み表示はFandom側のiframe/CSP方針にも依存します。
- **Wiki保存で画像が不足する:** Fandomの画像URL・Content-Type・サイズ制限に合わない画像は保存されません。保存ログをCLIで確認してください。
- **AIが利用できない:** 起動したPowerShellの環境に `GROQ_API_KEY` が設定されているか、Groq APIキーとアカウント状態を確認してください。
- **AIの回答が未確認を返す:** ローカル資料が不足している、オンライン検索に失敗した、または質問とページ本文が合致しない可能性があります。検索状態と回答の出典欄を確認してください。
- **Level図鑑にデータがない:** `extracted_fandom_levels/`、必要に応じて `downloaded_images/` と `parsed_index/` があるか確認してください。Wikiタブの利用にはこれらは不要です。
- **依存パッケージが見つからない:** GUI起動に使うPython環境を有効化してから `python -m pip install -r requirements.txt` を実行してください。PyInstaller版は依存をインストールした環境で再ビルドが必要です。

## ライセンスとコンテンツ利用

ソフトウェア本体はMIT Licenseです（[`LICENSE`](LICENSE)を参照）。Fandom上の記事・画像等の著作物はこのソフトウェアのライセンス対象ではありません。保存データの利用・再配布にあたっては、各作品・ページのライセンス、Fandomの利用規約、および権利者の条件を確認してください。本ソフトウェアはBackrooms Fandom JP公式とは関係ありません。
