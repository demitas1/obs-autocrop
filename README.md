# obs-autocrop

Python から OBS の Crop/Pad フィルタを制御し、ArUco マーカーでクロップ値を自動算出する実験プロジェクト。
設計方針は [PLAN.md](PLAN.md) を参照。

## セットアップ

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'        # GUI (Phase 3) も使う場合は '.[dev,gui]'
cp config.example.toml config.toml       # 環境に合わせて編集
.venv/bin/pytest
```

システム側の前提: Ubuntu 22.04 / 24.04、OBS Studio 28 以降、`v4l-utils`。

## 実験（Phase 1）

| スクリプト | 内容 | 状態 |
|---|---|---|
| `experiments/e0_inspect.py` | 実環境の検査（読み取り専用。出力は `experiments/out/`） | 完了 |
| `experiments/e3_devices.py` | カメラの恒久識別 | 完了 |
| `experiments/e2_scene_file.py` | シーンコレクションの読み書き | 未着手 |
| `experiments/e1_websocket.py` | websocket 通信 | 未着手 |
| `experiments/e4_aruco.py` | ArUco 認識 | 未着手 |

結果は [PLAN.md](PLAN.md) の 13章に記録する。

## ライセンス

GPL-3.0-or-later（[LICENSE](LICENSE)）。
依存する `obsws-python` と `PyQt6` が GPL-3.0-only のため、GPL v3 互換のライセンスとしている。
