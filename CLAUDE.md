# CLAUDE.md

設計方針・仕様の正は [PLAN.md](PLAN.md)。作業前に必ず 0章（実装方針）と 13章（実験結果記録）を確認する。

## 進め方
- Phase 1 の実験は E0 → E3 → E2 → E1 → E4 の順。進捗は PLAN.md 13章を参照（E0・E3・E2・E1・E4 完了。Phase 1 完了）。
- 実験で「要確認」を確定させたら、13章に追記し、該当する本文も更新する。本文と実環境が違えば実環境を正とする。
- `experiments/` は薄く保ち、ロジックは `src/obs_autocrop/{obsctl,platforms,vision}` に置く。
- OS 依存処理は `platforms/` に閉じ込め、共通層から `pyudev` や `v4l2-ctl` を直接使わない（PLAN.md 3.1）。
- 外部依存のない部分には pytest の単体テストを書く。

## 実環境に触れる操作のルール
- ファイル書き込みは既定 `--dry-run`、`--write` で書き込み。書き込み前にバックアップ。OBS 起動中は書き込まない。
- 次の操作は実行前に必ずユーザーに確認する: シーンコレクションへの `--write`（拒否されることの確認目的でも同様）、OBS の起動、配信・録画中の試験、`v4l2-ctl` によるコントロール変更、物理操作（マーカー設置、USB 抜き差し、再起動、目視確認）。
- OBS の設定ディレクトリ（websocket パスワードを含む）をリポジトリ内へコピーしない。出力ではパスワードをマスクする。

## 実環境（2026-11-08 時点、詳細は PLAN.md 13章）
- Ubuntu 24.04、OBS 32.2.0（PPA、`/usr/bin/obs`）、Python 3.12、OpenCV 5.0
- カメラ: Webcam AC410（USB 2.0）、`/dev/video0` がキャプチャ用。3840×2160 MJPG 30fps で運用（1080p 以上は MJPG 必須）。OBS のキャンバスも 3840×2160
- websocket サーバーは有効（認証あり、パスワードは OBS の config.json から読む）。トレイのアラートは無効にしておく（有効だと接続時にフレームが落ちる）
- OBS の配信先は検証用のローカル RTMP（`rtmp://127.0.0.1:1935/live`、キー `test`）。外部には配信されない
- Studio モード使用（「シーンを複製」オン）。キャンバスのサイズを変えたら `tools/fix_scale_ref.py` で各アイテムの `scale_ref` を合わせる
- OBS の操作（起動・終了・トランジション・プロパティ画面）はユーザーが行う。手順は一つずつ指示する
- カメラの映像は机に対して 180° 回転している（OBS ではシーンアイテムの負のスケールで戻している）
- カメラのフォーカスはオートのまま（ユーザー判断）。`[camera.controls]` で固定するのは zoom / pan / tilt のみ
- カメラを OpenCV で開く計測（`e4_aruco.py snap|measure`）は OBS の終了中に行う

## コマンド
```sh
.venv/bin/pip install -e '.[dev,gui]'
.venv/bin/pytest
.venv/bin/python experiments/e0_inspect.py   # 実環境の検査（読み取り専用）
.venv/bin/python experiments/e1_websocket.py status|probe --yes|live --yes   # websocket（probe/live は OBS を一時変更）
.venv/bin/python experiments/e2_scene_file.py inspect|set-device --mode M [--write]|restore BACKUP [--write]|verify   # シーンファイル
.venv/bin/python experiments/e3_devices.py list|check --label X|summary   # カメラ識別
.venv/bin/python experiments/e4_aruco.py markers --size-mm N|snap|measure [--runs 10]   # ArUco（snap/measure は OBS 終了中）
.venv/bin/python experiments/e4_aruco.py controls list|check|set --yes   # カメラコントロール（set は変更）
.venv/bin/python experiments/e4_aruco.py apply --yes|restore-crop FILE --yes   # 計測値の OBS への適用（OBS 起動中）
.venv/bin/python experiments/e5_layout.py status|apply --yes [--anchor A] [--transition]|restore FILE --yes   # シーンアイテムの自動配置
python3 tools/rtmp_sink.py serve [--discard] [--loop]   # 検証用ローカル RTMP 受信（ffmpeg）
python3 tools/rtmp_sink.py analyze FILE              # 映像ファイルのタイムスタンプ欠落検査
.venv/bin/python tools/fix_scale_ref.py [--write]    # キャンバス変更後の scale_ref 修正（OBS 終了中）
```
実験の出力は `experiments/out/`（git 管理外）。
手動で一連の流れ（計測 → クロップ適用 → 自動配置）を試す手順は `docs/manual-test.md`。

## 現状のメモ（2026-11-08）
- Phase 1（E0〜E4）完了。シーンアイテムの自動配置（PLAN.md 14章）も実装済み。次は Phase 2（`cli.py`、`launcher.py`、統合フロー。PLAN.md 8章）。
- カメラのシーンアイテムは自動配置（top-left、表示範囲 (0,0)-(3025,2160)）を適用し、プログラムにも反映済み。元の変換は `experiments/out/e5-transform-before-20261108-165940.json`
- OBS の Crop/Pad は E4 の計測値（x=904 y=66 w=2310 h=1649、`relative=false`）が適用された状態。適用前の設定は `experiments/out/e4-crop-before-*.json`（`restore-crop` で戻せる）。
- 未決定事項:
  - `framerate` を JSON に書くか（OBS のプロパティ画面の選択肢の値と一致せず、OK で `-1` に戻る。PLAN.md 9.1）
  - 計測結果の保存先（PLAN.md 7.5 の `~/.local/state/obs-autocrop/`）は Phase 2 で実装。E4 では `experiments/out/e4-last.json`
