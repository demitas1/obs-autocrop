# CLAUDE.md

設計方針・仕様の正は [PLAN.md](PLAN.md)。作業前に必ず 0章（実装方針）と 13章（実験結果記録）を確認する。

## 進め方
- Phase 1 の実験は E0 → E3 → E2 → E1 → E4 の順。進捗は PLAN.md 13章を参照（E0・E3・E2・E1 完了、次は E4）。
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
- カメラ: Webcam AC410（USB 2.0）、`/dev/video0` がキャプチャ用。3840×2160 MJPG 30fps で運用（1080p 以上は MJPG 必須）。OBS のキャンバスも 3840×2160
- websocket サーバーは有効（認証あり、パスワードは OBS の config.json から読む）。トレイのアラートは無効にしておく（有効だと接続時にフレームが落ちる）
- OBS の配信先は検証用のローカル RTMP（`rtmp://127.0.0.1:1935/live`、キー `test`）。外部には配信されない

## コマンド
```sh
.venv/bin/pip install -e '.[dev,gui]'
.venv/bin/pytest
.venv/bin/python experiments/e0_inspect.py   # 実環境の検査（読み取り専用）
.venv/bin/python experiments/e1_websocket.py status|probe --yes   # websocket（probe は OBS を一時変更）
.venv/bin/python experiments/e2_scene_file.py inspect|set-device --mode M [--write]|restore BACKUP [--write]|verify   # シーンファイル
.venv/bin/python experiments/e3_devices.py list|check --label X|summary   # カメラ識別
python3 tools/rtmp_sink.py serve [--discard] [--loop]   # 検証用ローカル RTMP 受信（ffmpeg）
python3 tools/rtmp_sink.py analyze FILE              # 映像ファイルのタイムスタンプ欠落検査
.venv/bin/python tools/fix_scale_ref.py [--write]    # キャンバス変更後の scale_ref 修正（OBS 終了中）
```
実験の出力は `experiments/out/`（git 管理外）。
