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

## ライセンス

GPL-3.0-or-later（[LICENSE](LICENSE)）。
依存する `obsws-python` と `PyQt6` が GPL-3.0-only のため、GPL v3 互換のライセンスとしている。
