# 手動テスト手順（マーカー認識 → クロップ算出 → OBS への適用）

Phase 1 の実験スクリプトを使って、統合フロー（[PLAN.md](../PLAN.md) 8章）と同じ流れを手動で実行する手順。
Phase 2 の `cli.py run` を作る前に、何度か繰り返して動作を確かめるために使う。

## 準備

- `config.toml` の `[camera]`、`[target]`、`[camera.controls]`、`[layout]` が設定済みであること（[config.example.toml](../config.example.toml)）。
- マーカーを印刷しておく。

```sh
.venv/bin/python experiments/e4_aruco.py markers --size-mm 30   # experiments/out/markers/ に PDF と PNG
```

  - 等倍（100%、ページに合わせない）で印刷し、ページ下の 50mm のスケールを定規で確認する。
  - 灰色の線で切り取る。白い余白（quiet zone）は残す。

## 手順（1 回分）

| # | OBS | 操作 | コマンド |
|---|---|---|---|
| 1 | 終了中 | マーカーを置く | — |
| 2 | 終了中 | 配置とピントの確認（省略可） | `.venv/bin/python experiments/e4_aruco.py snap` |
| 3 | 終了中 | 計測とクロップ値の計算 | `.venv/bin/python experiments/e4_aruco.py measure --runs 10 --label test1` |
| 4 | — | OBS を起動する | — |
| 5 | 起動中 | 起動後もカメラのコントロールが保持されているかの確認（省略可） | `.venv/bin/python experiments/e4_aruco.py controls check` |
| 6 | 起動中 | クロップを OBS に適用する（websocket） | `.venv/bin/python experiments/e4_aruco.py apply --yes` |
| 7 | 起動中 | シーンアイテムを自動配置する | `.venv/bin/python experiments/e5_layout.py apply --yes [--anchor top-center] [--transition]` |
| 8 | 起動中 | 目視で確認し、マーカーを片付ける | — |

### 1. マーカーの置き方

- 切り取りたい範囲の四隅に 1 枚ずつ置く。マーカーの黒い四角の角を範囲の角にぴったり合わせ、マーカーは範囲の内側に置く。
- ID と隅の組み合わせ、マーカーの向きは自由（画像上の座標から四隅を決める）。
- 範囲の辺をカメラ画像の辺となるべく平行にする。傾きが幅・高さの 1% を超えると警告が出る（処理は続く）。
- マーカーが浮いたり、手やペンで隠れたりしないようにする。

### 2. snap（配置とピントの確認）

- 1 枚撮影し、検出できた ID と各マーカーの大きさ（ピクセル）を表示する。
- `experiments/out/e4-<時刻>-snap.jpg` と `-snap-overlay.jpg`（検出結果を重ねた画像）を保存する。
- 4 枚とも検出されない場合は、置き方、照明（反射）、ピントを見直す。

### 3. measure（計測）

- `--runs` 回、カメラを開き直して計測する（1 回あたり約 2.4 秒）。
- 結果:
  - 各回のクロップ値と、フレーム間のばらつき（角の標準偏差）
  - 集計（各値の最小・最大・ばらつき）と、±1px 以内かどうかの判定（OK / NG）
  - 画像上の四隅と ID の対応
- 保存するもの:
  - `experiments/out/e4-last.json`: 最後の計測結果。手順 6 の `apply` はこれを使う
  - `e4-<時刻>-overlay.jpg`: 検出結果とクロップ矩形を重ねた画像
  - `e4-<時刻>-corners.png`: 各マーカーの外側の角を拡大した画像。緑線がクロップの境界、赤点が検出した角
- **NG のときも `e4-last.json` は書き出される**（終了コード 1）。そのまま手順 6 に進むと NG の値が適用されるので、計測し直す。

### 4. OBS の起動

- 計測（手順 2・3）は OBS の終了中に行う。OBS がカメラを使っていると開けないため。
- 1 回のテストごとに、OBS の終了と起動が 1 回ずつ必要になる。

### 6. apply（クロップの適用）

- 適用前に次を確認し、問題があれば中断する（`--force` で警告のみにできる）。
  - Crop/Pad より前に有効なフィルタがないこと
  - ソースの `device_id` が計測したカメラと同じこと
  - ソースの素のサイズが計測時の解像度と同じこと（確認のため Crop/Pad を一瞬無効にする。映像が一瞬切り替わる）
- 適用後、ソースのサイズがクロップ値と一致するかを確認し、スクリーンショットを `e4-<時刻>-applied.png` に保存する。
- 適用前のフィルタ設定を `e4-crop-before-<時刻>.json` に保存する。
- 設定は websocket で変更する（シーンコレクションの JSON は直接書き換えない）。変更は OBS の終了時に OBS 自身が保存する。

### 7. layout（シーンアイテムの自動配置）

- クロップ後のソースを、アスペクト比を保ってキャンバスの内側に収まる最大の大きさ（縦または横がいっぱい）にし、アンカーに合わせる。
  - `top-left`: 左上を左上に
  - `top-right`: 右上を右上に
  - `top-center`: 上辺中央を上辺中央に
- `--anchor` を省略すると `config.toml` の `[layout] anchor` を使う。
- `status` で、変更せずに現在の値と配置後の値を確認できる。

```sh
.venv/bin/python experiments/e5_layout.py status [--anchor top-right]
```

- 適用前の変換を `e5-transform-before-<時刻>.json`、シーンのスクリーンショットを `e5-<時刻>-scene.png` に保存する。
- **Studio モードではプレビューにのみ反映される。** プログラムへは OBS の画面でトランジションするか、`--transition` を付ける。配信中・録画中は出力が切り替わるので注意する。
- 回転・境界（bounds）を使うシーンアイテムには適用できない（エラーで中断する）。

## 元に戻す

```sh
.venv/bin/python experiments/e4_aruco.py restore-crop experiments/out/e4-crop-before-<時刻>.json --yes
.venv/bin/python experiments/e5_layout.py restore experiments/out/e5-transform-before-<時刻>.json --yes [--transition]
```

## 試すとよい条件

- マーカーの位置を変える（広い範囲・狭い範囲、縦長・横長）
- マーカーの向き、ID と隅の組み合わせを変える
- 照明を変える（反射が出る、暗い）
- `[layout] anchor` を 3 種類とも試す
- 範囲の一部が画面からはみ出す（範囲を画面内に切り詰めて警告を出す）

## 記録

- 計測と適用の結果は `experiments/out/e4-log.jsonl`、配置の結果は `experiments/out/e5-log.jsonl` に追記される。`--label` を付けると、どのテストか後で見分けやすい。
- 想定外の挙動や新しく分かったことは [PLAN.md](../PLAN.md) の 13章に記録する。
- `experiments/out/` は git 管理外。
