# CLAUDE.md

設計方針・仕様の正は [PLAN.md](PLAN.md)。作業前に必ず 0章（実装方針）と 13章（実験結果記録）を確認する。

## 進め方
- Phase 1 の実験は E0 → E3 → E2 → E1 → E4 の順。進捗は PLAN.md 13章を参照（E0・E3 完了、次は E2）。
- 実験で「要確認」を確定させたら、13章に追記し、該当する本文も更新する。本文と実環境が違えば実環境を正とする。
- `experiments/` は薄く保ち、ロジックは `src/obs_autocrop/{obsctl,platforms,vision}` に置く。
- OS 依存処理は `platforms/` に閉じ込め、共通層から `pyudev` や `v4l2-ctl` を直接使わない（PLAN.md 3.1）。
- 外部依存のない部分には pytest の単体テストを書く。

## 実環境に触れる操作のルール
- ファイル書き込みは既定 `--dry-run`、`--write` で書き込み。書き込み前にバックアップ。OBS 起動中は書き込まない。
- 次の操作は実行前に必ずユーザーに確認する: シーンコレクションへの `--write`、OBS の起動、配信・録画中の試験、`v4l2-ctl` によるコントロール変更、物理操作（マーカー設置、USB 抜き差し、再起動、目視確認）。
- OBS の設定ディレクトリ（websocket パスワードを含む）をリポジトリ内へコピーしない。出力ではパスワードをマスクする。

## 実環境（E0 時点、詳細は PLAN.md 13章）
- Ubuntu 24.04、OBS 32.2.0（PPA、`/usr/bin/obs`）、Python 3.12、OpenCV 5.0
- カメラ: Webcam AC410（USB 2.0）、`/dev/video0` がキャプチャ用。1080p は MJPG 必須
- websocket サーバーは無効（E1 の前にユーザーが有効化する）

## コマンド
```sh
.venv/bin/pip install -e '.[dev,gui]'
.venv/bin/pytest
.venv/bin/python experiments/e0_inspect.py   # 実環境の検査（読み取り専用）
.venv/bin/python experiments/e3_devices.py list|check --label X|summary   # カメラ識別
```
実験の出力は `experiments/out/`（git 管理外）。
