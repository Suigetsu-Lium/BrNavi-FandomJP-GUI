# BrNavi Fandom JP 更新プログラム

BrNavi本体とは別に起動する、Windows向けの独立した更新プログラムです。署名済みZIPパッチをバージョン順に適用します。更新を実行する前にBrNavi本体を終了してください。

## 更新プログラムのビルド

リポジトリのルートで次を実行します。

```bat
scripts\build_updater.bat
```

生成先は `dist\Updater\Updater.exe` です。ビルドにはPyInstallerとTkinterを利用できるPython環境が必要です。

## 逐次パッチの作成

まず `build_release.bat` で更新元・更新先それぞれの本体をビルドします。パッチ生成時に指定するのは、ソースコードのフォルダーや外側の作業フォルダーではなく、`BrNavi-GUI.exe` と `VERSION.txt` が直接入っている完成済み配布フォルダーです。

通常の更新パッチの作成例：

```bat
python -m updater.build_patch ^
  --from-dir "releases\v1.0.1\BrNavi-GUI" ^
  --to-dir "releases\v1.0.2\BrNavi-GUI" ^
  --output "artifacts\BrNavi-v1.0.1-to-v1.0.2.patch.zip" ^
  --signing-key "E:\SecureKeys\BrNavi-update-private.pem"
```

Updaterをまだ持っていない既存インストールへ初めて導入する場合は、ブートストラップ用パッチにUpdater.exeを含めます。

```bat
python -m updater.build_patch ^
  --from-dir "releases\v1.0.0\BrNavi-GUI" ^
  --to-dir "releases\v1.0.1\BrNavi-GUI" ^
  --bootstrap-updater "dist\Updater\Updater.exe" ^
  --output "artifacts\BrNavi-v1.0.0-to-v1.0.1-bootstrap.patch.zip" ^
  --signing-key "E:\SecureKeys\BrNavi-update-private.pem"
```

生成処理は、完成済みのPyInstaller onedirフォルダー全体を比較し、追加・置換・削除の操作を作成します。ファイル順を固定し、再現可能なZIPを生成します。Ed25519秘密鍵を必須とし、マニフェスト本体に対する署名を付けます。

SQLiteデータベース、ダウンロード済みWiki HTML・画像、索引、設定、キャッシュ、ログ、バックアップなどの利用者データ領域は、パッチの比較・適用対象から除外します。配置場所にかかわらず、データベース、SQLiteのWAL/SHM、ログ、認証情報、秘密鍵、PEM等のファイルも保護します。通常の差分では `Updater.exe` を対象外とし、初回導入時のブートストラップ指定でのみ追加できます。

生成したZIP・EXEはGitリポジトリへコミットせず、GitHub Releasesの配布ファイルとして公開します。

## パッチZIPの形式（v1）

署名付きパッチZIPには、`patch_manifest.json`、64バイトのEd25519署名 `patch_manifest.sig`、およびマニフェストが参照するファイルだけを格納します。署名対象は `patch_manifest.json` のUTF-8バイト列そのものです。

```json
{
  "patch_format": 1,
  "application": "BrNavi Fandom JP",
  "from_version": "1.0.1",
  "to_version": "1.0.2",
  "files": [
    {"path": "modules/example.py", "action": "replace", "sha256": "<64桁の小文字16進数>"},
    {"path": "modules/new_module.py", "action": "add", "sha256": "<64桁の小文字16進数>"},
    {"path": "old_module.py", "action": "delete"}
  ]
}
```

追加・置換ファイルの実体は `payload/<path>` に格納し、SHA-256で内容を検証します。削除操作にはペイロードもハッシュ値もありません。Updaterはマニフェストを解析・適用する前に署名を検証します。

Updaterはアプリ識別子、バージョン、Windowsで安全な相対パス、パスの重複・衝突、保護対象データ領域、ZIP内エントリー、ペイロードの存在・サイズ・ハッシュを検証します。インストール先のシンボリックリンク／ジャンクション経由のすり替えや、リダイレクトされた `VERSION.txt`・バックアップ先も拒否します。インストール済み `VERSION.txt` がパッチの更新元バージョンと一致しない場合は適用しません。

更新前に `update_backups/` へ既存ファイルを退避し、トランザクション情報とバックアップのSHA-256を記録します。必要なバックアップと適用中状態の記録が完了してからファイル操作を開始し、全ファイルの処理後に `VERSION.txt` を最後に更新します。途中で失敗した場合はロールバックを試みます。適用中またはロールバック中にUpdaterやWindowsが中断された場合、次回起動時にロールバックを再開します。準備中のトランザクションは、インストール先を変更していないことを確認できるため破棄できます。トランザクション情報やバックアップが欠落・破損・不一致の場合は、推測で処理を続けず更新を停止します。

コミット済み・ロールバック済みのバックアップは自動削除しません。ファイルとジャーナルの書き込みには `fsync` と原子的な置換を利用しますが、突然の電源断時におけるディレクトリエントリーの永続性はWindowsやファイルシステムの挙動に依存します。

初回ブートストラップパッチは `Updater.exe` を一度だけ追加できます。Updater自身は実行中に自分自身を更新できません。既存のv1.0.0環境へ導入する場合は、ブートストラップ配布物のUpdater.exeを一時フォルダーへ展開して起動し、`--install-dir` に既存の `BrNavi-GUI.exe` があるフォルダーを指定します。パッチ適用後、Updater.exeは本体と同じフォルダーに配置されます。

## 署名鍵の初期設定と管理

信頼できるオフライン環境で一度だけ鍵ペアを生成します。

```bat
python scripts\generate_update_signing_key.py --private-key "E:\SecureKeys\BrNavi-update-private.pem"
```

スクリプトが表示するBase64公開鍵を `updater/trusted_public_key.py` の `TRUSTED_PUBLIC_KEY_B64` に設定し、その公開鍵を組み込んだUpdater.exeをビルド・配布します。

**秘密鍵はリポジトリや配布ZIPへ絶対に含めないでください。** リポジトリ外の安全な場所に保管し、アクセスを制限したうえで安全なバックアップを維持してください。パッチ生成には `--signing-key` が必須です。署名がないパッチや署名が一致しないパッチはUpdaterが拒否します。公開鍵を変更した場合は、信頼できる配布経路で新しいUpdater.exeを配布する必要があります。

## セキュリティ上の仕様と制約

- データベース、Wiki HTML・画像、索引、設定、認証情報などの利用者データは、パッチ生成側と適用側の両方で保護します。
- SHA-256でペイロードの完全性を確認し、Ed25519署名でマニフェスト（バージョン遷移と各ペイロードのハッシュを含む）を認証します。
- Ed25519公開鍵は `updater/trusted_public_key.py` に固定します。Updaterが受け入れるのは、この公開鍵で検証できる署名付きパッチだけです。
- 公開前にはWindows実機で、初回ブートストラップ、連続したバージョン更新、プロセス検出、異常終了時のロールバックを確認してください。
