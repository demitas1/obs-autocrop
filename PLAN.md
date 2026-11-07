# Python による OBS クロップ制御（＋ArUco 自動検出）実験 — 企画書（詳細版 v1.3）

> ステータス: 詳細版。Claude Code での実装の指示書として使う。
> 「（要確認）」と記した項目は、実験で実際の挙動を確認してから確定させる。

---

## 0. Claude Code 向けの実装方針

- Phase 1 の実験は **E0 → E3 → E2 → E1 → E4 の順に** 実装する。E0 は実環境の検査（12章）。依存関係の少ないものから進めるため。
- 実験スクリプト（`experiments/`）は薄く保ち、ロジックは共通モジュール（`obsctl/`、`platforms/`、`vision/`）に置く。Phase 2 と Phase 3 でそのまま再利用するため。
- 実環境のファイルを書き換える処理には、次の3つのルールを守る。
  - 既定は `--dry-run` とし、書き込むには `--write` を明示する。
  - 書き込む前に必ずバックアップを取る。
  - OBS が起動中なら書き込まない。
- 外部依存（カメラ、OBS、udev）のない部分には pytest で単体テストを書く（11章）。
- 未確定事項（「要確認」）を実験で確認したら、結果を本書の「13. 実験結果記録」に追記する。
- **実環境の検査を優先する。** 次の記述は参考情報であり、正とするのは実環境で確認した内容とする。
  - シーンコレクションの構造（4.2）
  - 設定ファイルのパスと解決方法（4.1）
  - OBS の起動中チェックの方法（6.4）
  - 実装前に実際の環境を調べ、本書と違いがあれば実環境に合わせて実装し、違いを 13章に記録して本書を更新する。
  - 検査の手順やコマンドは `experiments/e0_inspect.py` にまとめ、再実行できるようにする（12章 E0）。
- **ユーザーの操作や確認が必要な場面では、実行前に必ず確認を取る。**
  - 物理的な操作: マーカーの設置、USB の抜き差しやポートの変更、再起動、目視による確認
  - 実環境に影響する操作:
    - シーンコレクションへの `--write`
    - OBS の起動
    - 配信中・録画中の適用試験（E1）
    - `v4l2-ctl` によるコントロールの変更
- **実環境の OBS 設定は、作業開始前に設定ディレクトリごとバックアップする。**
- **移植性を考慮する。** Linux 以外は対象外だが、OS に依存する処理は `platforms/` に分離し、他の OS 向けの実装を後から追加できる構造にする（3.1）。

---

## 1. 目的とスコープ

### 目的
- **本機能**: Python から OBS の Crop/Pad フィルタを制御する。websocket を主経路とする。
- **追加機能**: ArUco マーカーを検出してクロップ値を自動算出する。算出値は初期値として使い、OBS 上で確認しながら微調整する。

### 使用前提
- **利用環境**: スタジオなど、カメラを固定した撮影・配信環境
- **目的**: 収録や配信のたびに、カメラのクロップ位置などを手作業で調整する負担を減らす
- **対象外**: 移動するカメラや被写体の追従（リアルタイムトラッキングではない）
- **画質の優先順位**: フレームレートより解像度を優先する

### 前提から導かれる設計判断
- **計測は収録・配信の前に1回行う。** カメラが固定されているので、継続的に計測する必要はない。計測後にカメラを解放する方式Aと相性がよい。
- **計測は数十フレームかけてよい。** 被写体が静止しているので、ウォームアップや複数フレームの中央値に時間をかけ、精度を優先する。
- **値の再利用を前提とする。** 機材の配置が変わらなければ、前回の計測値（`apply --from-last`）で十分な場合が多い。マーカーを置かなくても運用できるようにする。
- **解像度を優先する。** フォーマットの既定値は最大解像度（1920×1080）とし、その解像度で使えるフォーマットとフレームレートを選ぶ。
  - USB 2.0 で 1080p の YUYV が低フレームレートしか出ない場合は、MJPEG を選ぶ。
  - それでもフレームレートが足りない場合でも、解像度は下げずに警告だけ出す。
- **カメラの状態を固定する。** ズーム、フォーカス、露出の固定（7.3）は、この前提を成り立たせるための必須の運用とする。

### スコープ内
- V4L2 カメラソース 1つに付いた Crop/Pad フィルタ 1つの制御
- カメラの恒久的な識別と、OBS の `device_id` の更新
- マーカーの生成、検出、クロップ値の算出
- CLI（Phase 2）と PyQt の GUI（Phase 3）

### スコープ外
- 台形補正、回転補正、スキュー補正
- クロップ値の JSON への直接書き込み（クロップの適用は websocket のみで行う）
- 複数カメラの同時制御
- 移動するカメラや被写体への追従、配信中の継続的な自動再計測
- Linux 以外の OS

---

## 2. 決定事項

| 項目 | 決定内容 |
|---|---|
| 方式 | 方式A。計測し、カメラを解放してから OBS を起動し、websocket で値を適用・微調整する |
| 適用経路 | websocket（obs-websocket v5） |
| JSON の扱い | 読み取りと、`device_id`・解像度など V4L2 ソースの設定の更新に限る |
| カメラの識別 | 恒久識別子（`by-id` → `by-path` → VID:PID）で行う |
| `device_id` の書き込み | `resolved` / `link` / `none` を切り替え可能にする |
| クロップ基準 | マーカーの外側の角（マーカーはクロップ後のフレームに映る） |
| 幾何 | 軸平行矩形のみ |
| UI | 実験と Phase 2 は CLI、Phase 3 は PyQt6 |
| ライセンス | GPL-3.0-or-later。`obsws-python` と `PyQt6` が GPL-3.0-only のため（他の依存は Apache-2.0 / BSD / MIT / LGPL で互換） |

---

## 3. 環境

### システム
- Ubuntu 22.04 / 24.04
- OBS Studio 28 以降（obs-websocket v5 内蔵）
  - 推奨は PPA 版（`ppa:obsproject/obs-studio`）
  - Flatpak 版にも、設定のパスと起動コマンドを変えることで対応する（4.1）
- `v4l-utils`（`v4l2-ctl`）

### Python
- Python 3.10 以降（3.10 では `tomli` を使い、3.11 以降は標準の `tomllib` を使う）
- 依存パッケージ:

| パッケージ | 用途 | 備考 |
|---|---|---|
| `obsws-python` | websocket クライアント | |
| `opencv-python-headless` (>=4.8) | キャプチャ、ArUco | **headless 版を使う**。通常版は同梱の Qt プラグインが PyQt6 と衝突するため。ArUco は 4.7 以降、本体の `cv2.aruco.ArucoDetector` で使える |
| `numpy` | | |
| `pyudev` | カメラの識別 | |
| `psutil` | OBS 起動中かどうかの検知 | |
| `Pillow` | マーカーの PDF 出力 | |
| `PyQt6` | GUI | Phase 3 から |
| `pytest` | テスト | 開発用 |

- 依存関係は `pyproject.toml` で管理し、仮想環境（`venv` または `uv`）を使う。
- OS に依存するパッケージ（`pyudev` など）は、環境マーカー（`sys_platform == "linux"`）付きで宣言し、他の OS ではインストールされないようにする。

### 3.1 移植性（プラットフォーム抽象化）

#### 方針
- 対象は Linux のみだが、OS に依存する処理は **インターフェースと実装に分ける**。
- 共通層は OS に依存しない。共通層とは、websocket 制御、クロップ算出、マーカー検出、JSON の汎用的な読み書き、CLI、GUI を指す。
- 共通層からは `platforms.get_platform()` 経由でのみ OS 依存の機能を使う。共通層で `import pyudev` や `v4l2-ctl` の呼び出しを直接書かない。
- 未実装の OS では、`get_platform()` が `UnsupportedPlatform` を送出する。起動時に分かりやすいメッセージを出して終了する。
- パスは `pathlib` で扱い、区切り文字やホームディレクトリをハードコードしない。

#### OS 依存部分の一覧

| インターフェース | 役割 | Linux 実装 | 他 OS で想定される実装（参考） |
|---|---|---|---|
| `CameraLocator` | カメラの列挙と、恒久識別子による解決 | pyudev、`/dev/v4l/by-*` | Windows: デバイスインスタンスパス。macOS: AVFoundation の uniqueID |
| `CameraControls` | ズーム・フォーカス・露出の取得と設定 | `v4l2-ctl` | Windows: DirectShow の IAMCameraControl。macOS: UVC 制御ライブラリ |
| `FormatProber` | 対応する解像度・フォーマット・フレームレートの列挙 | `v4l2-ctl --list-formats-ext` | 各 OS の API |
| `CaptureBackend` | OpenCV のバックエンドと、デバイスの開き方 | `CAP_V4L2` とデバイスパス | `CAP_DSHOW` / `CAP_MSMF`、`CAP_AVFOUNDATION` とインデックス |
| `ObsPaths` | OBS の設定ルート、シーンコレクション、現在のコレクション名、websocket 設定の場所 | `~/.config/obs-studio`、Flatpak のパス | `%APPDATA%\obs-studio`、`~/Library/Application Support/obs-studio` |
| `ObsProcess` | OBS の起動中チェックと起動 | psutil、`obs` / `flatpak run` | `obs64.exe`、`open -a OBS` |
| `CaptureSourceAdapter` | OBS のカメラソースの種別と、設定値のデコード・エンコード（`device_id`、解像度、フォーマット） | `v4l2_input` | `dshow_input`、`macos-avcapture` など（設定キーと形式が異なる） |

- 共通層は、OBS のカメラソースを `CaptureSourceAdapter` 経由の中立的なモデルで扱う。
  - モデルは `CameraSourceConfig(device_ref, width, height, fourcc, fps)` とする。
  - これにより、`resolution` のパック形式のような OS 固有の表現は Linux 実装の中に閉じ込める。
- カメラの識別子も、中立的なモデル `CameraIdentity(kind, value, vendor_id, product_id, serial, name)` で扱う。`config.toml` の `[camera]` はこのモデルに読み込む。
- 実装の切り替え:
  - `get_platform()` が `sys.platform` に応じて実装を返す。
  - `config.toml` の `[platform]` で、個々の実装を差し替えられるようにする。例えば `obs_flavor = "flatpak"`、または `"native"`。
  - テストではモック実装を注入する。

---

## 4. OBS の管理体系と本アプリでの扱い

### 4.1 設定ファイルの場所

> 参考情報。実際のパスと解決方法は実環境で確認する（E0）。実装は `ObsPaths` に置く。


| 種別 | PPA 版 | Flatpak 版 |
|---|---|---|
| 設定ルート | `~/.config/obs-studio/` | `~/.var/app/com.obsproject.Studio/config/obs-studio/` |
| シーンコレクション | `basic/scenes/<名前>.json` | 同左 |
| 現在のコレクション名 | `user.ini`（OBS 31 以降）または `global.ini` の `[Basic] SceneCollectionFile` | 同左 |
| websocket 設定 | `plugin_config/obs-websocket/config.json`（要確認） | 同左 |
| 起動コマンド | `obs` | `flatpak run com.obsproject.Studio` |

- `config.toml` の `obs.config_dir` と `obs.launch_cmd` で切り替える。
- websocket の接続情報（ポート、パスワード）は `config.toml` に書くのを基本とする。省略した場合は `plugin_config/obs-websocket/config.json` から読む（キー名は E1 で要確認）。

### 4.2 シーンコレクション JSON の構造（必要な部分のみ）

> 参考情報。実環境のファイルを検査し、違いがあれば実環境に合わせる（E0）。V4L2 ソース固有の部分は `CaptureSourceAdapter` の Linux 実装に置く。


```jsonc
{
  "current_scene": "Scene",
  "sources": [
    {
      "name": "Camera",              // コレクション内で一意
      "uuid": "....",                // OBS 30 以降
      "id": "v4l2_input",            // ソースの種別
      "settings": {
        "device_id": "/dev/video0",
        "input": 0,
        "pixelformat": 1196444237,   // FourCC（リトルエンディアンの整数）
        "resolution": 125830200,     // (幅 << 16) | 高さ
        "framerate": 65566,          // (分子 << 16) | 分母、65566 = 1/30 → 30fps
        "...": "..."
      },
      "filters": [
        {
          "name": "Crop/Pad",        // ソース内で一意
          "id": "crop_filter",
          "enabled": true,
          "settings": { "relative": false, "left": 0, "top": 0, "cx": 1920, "cy": 1080 }
        }
      ]
    },
    { "name": "Scene", "id": "scene", "settings": { "items": [ { "name": "Camera", "...": "..." } ] } }
  ]
}
```

- シーンも `sources` の要素であり、`id: "scene"` で区別される。シーンアイテムは `settings.items` にある。
- `resolution`、`pixelformat`、`framerate` が `-1` の場合は「変更しない」を意味する（9章）。
- 未知のキーはすべて保持し、変更するキー以外には手を触れない。

### 4.3 対象の指定
- 対象は `source_name` と `filter_name` の組で指定する。`source_uuid` も指定できるようにし、指定されていればこちらを優先する（名前の変更に強くするため）。
- Crop/Pad フィルタが存在しない場合は、作成しない。エラーにして、OBS の画面で追加するよう案内する（JSON や websocket での自動作成は将来の拡張とする）。

---

## 5. カメラの恒久識別（`platforms/linux/camera.py`、`CameraLocator` の Linux 実装）

### 5.1 列挙
- `pyudev.Context().list_devices(subsystem="video4linux")` で列挙する。
- 映像キャプチャ用のノードだけを残す。
  - `ID_V4L_CAPABILITIES` に `:capture:` を含むこと
  - 属性 `index` が `0` であること（メタデータ用ノードを除外するため）
- 各デバイスについて次の情報を取得する。

| 項目 | 取得元 |
|---|---|
| ノード | `device.device_node`（`/dev/videoN`） |
| 名前 | 属性 `name` |
| VID / PID | `ID_VENDOR_ID` / `ID_MODEL_ID` |
| シリアル | `ID_SERIAL_SHORT`（存在しない場合がある） |
| by-id | `device.device_links` のうち `/dev/v4l/by-id/` で始まるもの |
| by-path | `device.device_links` のうち `/dev/v4l/by-path/` で始まるもの |

### 5.2 識別子と解決
- `config.toml` の `[camera]` で、次のいずれかを指定する。

```toml
[camera]
by_id   = "usb-046d_C922_Pro_Stream_Webcam_ABCD1234-video-index0"   # 優先度 1
# by_path = "pci-0000:00:14.0-usb-0:2:1.0-video-index0"             # 優先度 2
# vendor_id = "046d"; product_id = "085c"; name = "C922 Pro Stream Webcam"  # 優先度 3
```

- `config.toml` の `[camera]` の各項目は、`CameraIdentity` に読み込む。
  - `by_id` は `kind="by-id"`、`by_path` は `kind="by-path"`、`vendor_id` などは `kind="usb"` として扱う。
- 解決の結果は `ResolvedCamera(node, link, info)`（`models.py`、OS 非依存）として返す。`node` は `CaptureBackend` と `CaptureSourceAdapter` にそのまま渡せるデバイス指定とする。
  - `link` は識別に使った恒久パス。優先度 3 で解決した場合は、そのデバイスの `by-id` または `by-path` が入る。
- 一致するデバイスが 0 件なら `CameraNotFound` を、2 件以上なら `CameraAmbiguous` を送出し、候補を表示して中断する。
- CLI の `devices list` で候補を一覧表示し、そのまま `config.toml` に貼り付けられる形式の候補設定も出力する。

### 5.3 `device_id` 書き込みモード

| モード | 書き込む値 | 用途 |
|---|---|---|
| `resolved`（既定） | `ResolvedCamera.node` | OBS 標準と同じ形式。毎回本アプリ経由で起動する前提 |
| `link` | `ResolvedCamera.link` | OBS を手動で起動しても正しいカメラを掴む |
| `none` | 書き換えない | 現在の `device_id` を `realpath` で解決し、`node` と一致するか検証する。不一致なら警告する |

- 既定値は `camera.device_id_mode` で決め、CLI の `--device-id-mode` で上書きする。

---

## 6. OBS 制御（`obsctl/`）

### 6.1 `scene_file.py`
- `load(collection) -> SceneCollection`
  - ファイルの場所と、コレクション名を省略した場合の現在のコレクション名は `ObsPaths` から得る。
- `find_source(name | uuid) -> SourceEntry`
- `get_camera_config(source) -> CameraSourceConfig`
  - デコードは `CaptureSourceAdapter` に委ねる。Linux 実装は `v4l2_input` の `resolution` などをデコードし、`-1` の項目は `None` とする。
- `set_camera_config(source, config: CameraSourceConfig)`
  - エンコードは `CaptureSourceAdapter` に委ねる。変更内容は差分として表示する。
- `scene_file.py` 自体は、JSON の汎用的な読み書き、ソースとフィルタの検索、バックアップ、保存だけを担い、OS 非依存とする。
- `save(path, *, write: bool)`
  - `write=False` なら差分の表示のみ行う。
  - `write=True` なら次の手順で保存する。
    1. OBS が起動中でないことを確認する（6.4）。
    2. `backups/<コレクション名>.<タイムスタンプ>.json` にバックアップを取る。
    3. 一時ファイルに書き出し、`os.replace` で置き換える。
  - JSON は `ensure_ascii=False` で書き出し、キーの順序を保持する。

### 6.2 `ws_client.py`（obsws-python のラッパー）

| メソッド | 使う websocket リクエスト | 用途 |
|---|---|---|
| `connect(timeout)` | `GetVersion` | 接続とバージョンの確認 |
| `wait_ready(timeout=30)` | `GetVersion` を 0.5 秒間隔で再試行 | OBS 起動直後の接続待ち |
| `list_v4l2_inputs()` | `GetInputList(inputKind="v4l2_input")` | |
| `get_input_settings(name)` | `GetInputSettings` | `device_id` などの確認 |
| `list_filters(source)` | `GetSourceFilterList` | フィルタの順序（`filterIndex`）と種別の確認 |
| `get_crop(source, filter)` | `GetSourceFilter` | |
| `set_crop(source, filter, rect)` | `SetSourceFilterSettings(overlay=True)` | クロップの適用 |
| `find_scene_item(source)` | `GetSceneList` と `GetSceneItemList` | ソースを含むシーンとシーンアイテム ID の取得 |
| `get_source_size(source)` | `GetSceneItemTransform` | `sourceWidth` / `sourceHeight` の取得 |
| `wait_source_size(source, timeout=15)` | 上記を再試行 | 起動直後はサイズが 0 になるため、0 以外になるまで待つ |
| `set_filter_enabled(...)` | `SetSourceFilterEnabled` | 整合チェック時に一時的に無効化する |
| `screenshot(source, width)` | `GetSourceScreenshot` | GUI での確認用プレビュー |

- **要確認（E1）**: `sourceWidth` / `sourceHeight` はフィルタ適用後のサイズか、適用前のサイズか。
  - 適用後のサイズだった場合、整合チェックでは Crop フィルタを一時的に無効化してから素のサイズを取得する。
  - あるいは、現在のクロップ値から逆算して素のサイズを求める。

### 6.3 クロップ値の表現
- 内部では `CropRect(x, y, w, h)` をソースのピクセル座標で扱う。
- OBS への設定は `relative=false` とし、`left=x`、`top=y`、`cx=w`、`cy=h` で送る。
  - **要確認（E1）**: `relative=false` のとき `cx` / `cy` が幅と高さとして解釈されること。
- 送信前に検証する。
  - `0 <= x`、`0 <= y`、`x + w <= W`、`y + h <= H`、`w >= 16`、`h >= 16`
  - 条件を満たさなければ値を範囲内に収め、警告を出す。

### 6.4 `launcher.py`

> 起動中チェックと起動コマンドは `ObsProcess` に置き、`launcher.py` はそれを呼ぶだけにする。以下は Linux 実装の想定であり、実環境で確認する（E0）。

- `is_obs_running() -> bool`
  - psutil でプロセス名 `obs` を探す。Flatpak 版では `cmdline` に `com.obsproject.Studio` を含むかも確認する。
- `launch(collection, scene=None, extra_args=())`
  - `launch_cmd + ["--collection", collection] (+ ["--scene", scene])` を実行する。
  - `subprocess.Popen(..., start_new_session=True)` で起動し、出力は `logs/obs-<タイムスタンプ>.log` に書き出す。
  - 前回の終了が異常だった場合に出る起動ダイアログを避けるため、`--disable-shutdown-check` を付けるかどうかを `config.toml` の `obs.extra_args` で選べるようにする（OBS 30 以降、要確認）。
- OBS の終了は本アプリでは行わない（ユーザーが操作する）。

---

## 7. 計測（`vision/`）

### 7.1 マーカー仕様と生成（`markers.py`）
- 辞書は `DICT_4X4_50` を使い、四隅の ID を次のように割り当てる。
  - 0: 左上
  - 1: 右上
  - 2: 右下
  - 3: 左下
- 推奨サイズの計算:
  - `mm_per_px = max(view_w_mm / 1920, view_h_mm / 1080)`（`view_*` はカメラに映る範囲）
  - `size_mm = target_px（既定 50）× mm_per_px` を、1 mm 単位で切り上げる
- 出力:
  - マーカー 4 枚を 1 ページに配置した PDF（300dpi、実寸）と、個別の PNG
  - quiet zone は 1 セル以上（既定 2 セル）とし、外周に薄い切り取り線を描く
- CLI の例: `markers generate --view 600x340 [--target-px 50] [--size-mm N]`

### 7.2 キャプチャ（`vision/capture.py`）

> `vision/capture.py` は OS 非依存の手順（設定順序、検証、ウォームアップ、解放）を担う。デバイスの開き方とバックエンドの指定は `CaptureBackend`（Linux 実装: `platforms/linux/capture.py`）に委ねる。

1. `CaptureBackend` が返すバックエンドとデバイス指定でデバイスを開く。Linux 実装は `cv2.VideoCapture(node, cv2.CAP_V4L2)`。
2. 次の順で設定する。
   1. `CAP_PROP_FOURCC`
   2. `CAP_PROP_FRAME_WIDTH` / `CAP_PROP_FRAME_HEIGHT`
   3. `CAP_PROP_FPS`
3. 設定値は、OBS 側の値（9章の決定規則）を使う。
4. 実際の `fourcc` と、取得したフレームの `shape` を確認する。一致しなければ `FormatMismatch` を送出する。
5. ウォームアップとして、最初の `warmup_frames`（既定 30）フレームを捨てる。露出を安定させるため。
6. `cap.release()` を `finally` で必ず呼ぶ。解放したあと、デバイスが開けることを確認してから次の処理へ進む。

### 7.3 カメラコントロールの固定（`CameraControls` の Linux 実装: `platforms/linux/controls.py`）
- `config.toml` の `[camera.controls]` に書いた値を、`v4l2-ctl -d <node> --set-ctrl=k=v,...` で設定する。
  - 計測前に設定し、OBS 起動後に再度読み出して、値が保持されているかを確認する。
- コントロール名はカメラやカーネルのバージョンで異なる（例: `focus_automatic_continuous` と `focus_auto`）。`devices controls <識別子>` で一覧（`--list-ctrls`）を表示できるようにする。
- **要確認（E4）**: OBS の V4L2 ソースが起動時に自前の値でコントロールを上書きするかどうか。

### 7.4 検出とクロップ算出（`detect.py`）
1. `cv2.aruco.ArucoDetector` を使う。パラメータは `cornerRefinementMethod = CORNER_REFINE_SUBPIX` とする。
2. `measure_frames`（既定 10）フレームについて検出し、ID 0〜3 がすべて揃ったフレームだけを採用する。採用できたフレームが `min_valid`（既定 5）未満なら失敗とする。
3. 各マーカーの外側の角を取り出す。OpenCV のマーカー角は、マーカー自身の向きで 左上・右上・右下・左下 の順に並ぶ。
   - ID0 → 角 0
   - ID1 → 角 1
   - ID2 → 角 2
   - ID3 → 角 3
4. 各角の座標について、フレーム間の中央値を取る。
5. 矩形を求める。
   - `left = min(P0.x, P3.x)`
   - `right = max(P1.x, P2.x)`
   - `top = min(P0.y, P1.y)`
   - `bottom = max(P2.y, P3.y)`
6. 余白 `margin_px`（既定 0）を外側に加え、座標は外側へ丸める（左上は `floor`、右下は `ceil`）。そのうえでフレームの範囲内に収める。
7. 傾きのチェック:
   - `|P0.x - P3.x|`、`|P1.x - P2.x|`、`|P0.y - P1.y|`、`|P3.y - P2.y|` のいずれかが `skew_tol`（既定は幅または高さの 1%）を超えたら警告する。
   - 回転やスキューは補正しないため、警告にとどめて処理は続ける。
8. 結果を `Measurement` として返す。中身は、各角の座標、フレーム間のばらつき（標準偏差）、`CropRect`、警告の一覧。

### 7.5 計測結果の保存
- 保存先は `~/.local/state/obs-autocrop/last_measurement.json`。
- 保存する内容:
  - タイムスタンプ
  - カメラの識別子と解決結果（`node`）
  - 解像度、`fourcc`、`fps`
  - 各角の座標
  - `CropRect`
  - 警告
- CLI の `apply --from-last` で、この値を再適用できる。

---

## 8. 統合フロー（Phase 2、`cli.py run`）

```
 1. 設定を読み込む
 2. OBS が起動中なら中断する（「OBS を終了してから実行してください」）
 3. カメラを解決する（5章）
 4. シーンコレクションを読み、対象ソースとフィルタを確認する
 5. フォーマットを決定する（9章の決定規則）
 6. V4L2 コントロールを設定する
 7. キャプチャと検出を行い、計測結果を保存する
 8. カメラを解放する
 9. JSON を更新する（device_id はモードに従う。フォーマットは -1 の場合のみ）
    ※ --dry-run では差分の表示のみ
10. OBS を起動し、wait_ready で接続を待つ
11. 整合チェック（9章）を行う。失敗したら適用せずに終了する
12. クロップ値を set_crop で適用する
13. 微調整モードに入る（--no-adjust で省略できる）
```

### 微調整モード（CLI）
- 1 行ごとにコマンドを入力すると、即座に適用する。

| 入力 | 動作 |
|---|---|
| `x+2` / `y-1` / `w+10` / `h-4` | 各値を増減する |
| `set 100 50 1280 720` | 4 つの値をまとめて指定する |
| `show` | 現在値を表示する |
| `reset` | 計測値に戻す |
| `save` | 現在値を計測結果として保存する |
| `q` | 終了する |

### CLI コマンド一覧

| コマンド | 内容 |
|---|---|
| `devices list` | カメラ候補の一覧と、設定の候補を表示する |
| `devices controls` | V4L2 コントロールの一覧を表示する |
| `obs inspect` | シーンコレクションの対象ソースとフィルタを、デコードした値で表示する |
| `obs status` | websocket の接続、ソースサイズ、現在のクロップ値を表示する |
| `markers generate` | マーカーを生成する |
| `measure` | 7章の計測のみ行う（OBS 停止中に実行） |
| `apply` | クロップ値を適用する（`--from-last` または `--rect x,y,w,h`） |
| `adjust` | 微調整モードのみを起動する |
| `run` | 上記の統合フローを実行する |

- 共通オプション: `--config`、`--device-id-mode`、`--dry-run` / `--write`、`-v`

---

## 9. 解像度・ピクセルフォーマットの整合

### 9.1 フォーマットの決定規則（計測前）
1. OBS ソースの `resolution`、`pixelformat`、`framerate` が指定されていれば（`-1` でなければ）、その値を使う。
2. `-1` の項目は、`config.toml` の `[camera.format]` の既定値を使い、警告を出す。`--write` を指定した場合は JSON にも書き込む。
3. 既定値は「解像度優先」とする。指定した解像度で使えるフォーマットのうち、目標のフレームレートを満たすものを選ぶ。満たすものがなければ、最もフレームレートの高いものを選んで警告する（解像度は下げない）。
4. 上記の値がカメラのサポート範囲外なら中断する。サポート範囲は `FormatProber` で取得する（Linux 実装は `v4l2-ctl --list-formats-ext` の出力をパースする）。
5. FourCC を OpenCV に渡すときは、次のように扱う。
   - `YUYV` と `MJPG` はそのまま渡す。
   - それ以外（`YU12`、`NV12` など）は試しに設定してみて、反映されなければ中断する。

### 9.2 OBS 起動後の整合チェック
1. `wait_source_size` で、ソースのサイズが 0 以外になるまで待つ。
2. 素のサイズを取得し（6.2 の「要確認」に従う）、計測時の解像度と一致するか確認する。
3. `list_filters` で、Crop/Pad より前（`filterIndex` が小さい側）に映像系のフィルタがないことを確認する。
4. `get_input_settings` で、`device_id` が期待どおりの値か確認する。
5. いずれかが満たされなければ、内容を表示して適用を中断する。`--force` を指定した場合は警告のみで適用する。

### 9.3 リスクの一覧

| リスク | 内容 | 対策 |
|---|---|---|
| 解像度の不一致 | OpenCV の既定値が OBS の設定と異なる。解像度ごとに画角が変わる機種がある | 同一解像度で計測する（9.1）。取得したフレームの実寸を検証する（7.2） |
| 設定の無視 | `cap.set()` が失敗を返さずに無視されることがある | 実際の値を検証する（7.2） |
| フォーマットの不一致 | MJPEG と YUYV で使える解像度が違う。画角が変わる可能性もある | FourCC を先に設定し、OBS と揃える |
| OBS 側が `-1` | デバイスの現状がそのまま使われる | 既定値を使い、JSON に明示的に書き込む（9.1） |
| 前段のフィルタ | 座標系が変わる | フィルタ順序を確認する（9.2） |
| V4L2 コントロール | ズームやフォーカスの変化で画角が変わる | 固定して、保持されているか確認する（7.3） |

---

## 10. Phase 3: PyQt6 GUI

### 10.1 画面構成
- **左ペイン（プレビュー）**
  - 計測時: カメラ映像に、検出したマーカーの枠、外側の角、クロップ矩形を重ねて表示する。
  - 適用後: `GetSourceScreenshot` で取得した OBS 側の映像（クロップ後）を 2〜5fps で表示する。
- **右ペイン（操作）**
  - カメラ情報: 識別子、ノード、フォーマット
  - 計測の状態
  - `x` / `y` / `w` / `h` のスピンボックス（矢印キーで 1px、Shift と組み合わせて 10px ずつ変更）
  - 「計測」「OBS 起動」「適用」「計測値に戻す」「保存」の各ボタン
  - ログ表示

### 10.2 状態遷移

```
Idle → Measuring → Measured → Launching → Connected ⇄ Adjusting
                                   ↓
                              Error（いずれの状態からも遷移し得る）
```

- カメラは `Measured` へ遷移する時点で必ず解放する。

### 10.3 実装方針
- キャプチャ、websocket 通信、OBS の起動待ちは `QThread`（ワーカー）で行い、Signal と Slot で UI に結果を渡す。
- スピンボックスの変更は 100ms のデバウンスをかけてから `set_crop` する。
- ロジックは `obsctl/`、`platforms/`、`vision/` を再利用し、GUI 層は表示と状態管理だけを担う。

---

## 11. テスト方針

| 対象 | 方法 |
|---|---|
| 数値のデコード（解像度、フレームレート、FourCC） | 既知の値で単体テストする（例: 65566 → 30fps） |
| シーンコレクション | 実ファイルを匿名化したフィクスチャで、読み込み、更新、差分、保存（未知キーの保持）を確認する |
| クロップ算出 | 合成画像で確認する。生成したマーカーを既知の位置に貼った画像を作り、ノイズやぼかし、わずかな傾きを加えて、算出値の誤差が ±1px 以内であることを確認する |
| カメラの解決 | pyudev をモックし、一致 0 件・1 件・複数件の場合を確認する |
| プラットフォーム分離 | 共通層のテストはモックの `Platform` で実行し、Linux 以外の環境でも通ることを確認する。共通層から OS 依存モジュールを直接 import していないことを、静的チェック（import の検査）で確認する |
| CropRect の検証 | 範囲外の値や最小サイズ未満の値を与えて確認する |
| websocket | obsws-python をモックした単体テストと、実機で手動実行する E1 スクリプトの組み合わせ |

---

## 12. Phase 1 実験の詳細と完了条件

### E0: 実環境の検査（`experiments/e0_inspect.py`）

最初に実行する。結果をもとに、本書の参考情報（4.1、4.2、6.4）との違いを確認する。

- **内容**
  - OBS のインストール形態（PPA / Flatpak / その他）とバージョン
  - 設定ルートの位置と、現在のコレクション名の取得元（`user.ini` / `global.ini`）
  - websocket 設定ファイルの場所とキー名
  - シーンコレクション JSON の実際の構造。対象ソースの `settings` とフィルタのキー一覧を出力する
  - OBS 起動中のプロセス名と `cmdline`。起動中チェックの判定方法を決めるため
  - カメラデバイス、udev の属性、`/dev/v4l/by-*` の状態
- **完了条件**
  - 本書との違いを 13章に記録し、必要に応じて本書と実装方針を更新している。

### E3: カメラの恒久識別（`experiments/e3_devices.py`）
- **内容**
  - 列挙と、識別子の解決
  - USB の抜き差し、ポートの変更、再起動を行ってから再度解決する
- **完了条件**
  - 3 つの識別方式すべてで、正しい `/dev/videoN` に解決できる。
  - `by-id` ではポートを変えても、`by-path` では同じポートである限り、同じカメラに解決できる。

### E2: シーンコレクションの読み書き（`experiments/e2_scene_file.py`）
- **内容**
  - 対象ソースの設定を読み取り、デコードする。`-1` の検出も行う。
  - 3 つの書き込みモードで `device_id` を更新し、OBS を起動して確認する。
  - `link` モードで、プロパティ画面に何が表示されるか、再選択したときにどうなるかを確認する。
- **完了条件**
  - 各モードの後、OBS で正しいカメラが映る。
  - バックアップから元に戻せる。
  - 未知のキーが保持されている。

### E1: websocket 通信（`experiments/e1_websocket.py`）
- **内容**
  - 接続（接続情報を `config.json` から読み取れるかも含む）
  - ソースとフィルタの列挙、クロップ値の取得と設定
  - `relative=false` での `cx` / `cy` の解釈
  - `sourceWidth` がフィルタ適用前と適用後のどちらのサイズか
  - 配信中・録画中の適用
  - `GetSourceScreenshot` がフィルタ適用後の映像を返すか
- **完了条件**
  - 6.2 と 6.3 の「要確認」がすべて確定している。
  - 配信中・録画中に適用しても、出力が途切れない。

### E4: ArUco 認識（`experiments/e4_aruco.py`）
- **内容**
  - E3 と E2 の結果を使ってフォーマットを決め、キャプチャする。
  - 実寸を検証し、検出してクロップ値を算出する。
  - 検出結果を重ねた画像を保存する。
  - V4L2 コントロールを固定し、OBS 起動後も保持されているか確認する。
- **完了条件**
  - 10 回の計測で、算出値のばらつきが ±1px 以内に収まる。
  - その値を OBS に適用したとき、マーカーの外側の角がフレームの端に一致する（目視で確認）。

---

## 13. 実験結果記録

（実験ごとに追記する。要確認事項の結論、想定外の挙動、仕様の変更点）

| 日付 | 実験 | 結果 | 本書への反映 |
|---|---|---|---|
| | | | |

---

## 付録A: ディレクトリ構成

```
obs-autocrop/
├── pyproject.toml
├── config.example.toml
├── PLAN.md
├── src/obs_autocrop/
│   ├── config.py
│   ├── cli.py
│   ├── models.py         # CameraIdentity, CameraSourceConfig, CropRect など（OS 非依存）
│   ├── obsctl/           {scene_file.py, ws_client.py, launcher.py}   # OS 非依存
│   ├── platforms/
│   │   ├── __init__.py   # get_platform()、UnsupportedPlatform
│   │   ├── base.py       # インターフェース（typing.Protocol）
│   │   ├── mock.py       # テスト用
│   │   └── linux/        {camera.py, controls.py, formats.py, capture.py, obs_paths.py, obs_process.py, v4l2_source.py}
│   ├── vision/           {markers.py, capture.py, detect.py}         # OS 非依存
│   └── gui/      {app.py, main_window.py, workers.py}   # Phase 3
├── experiments/  {e0_inspect.py, e1_websocket.py, e2_scene_file.py, e3_devices.py, e4_aruco.py}
├── tests/        {fixtures/, test_*.py}
├── backups/      # .gitignore に追加
└── logs/         # .gitignore に追加
```

## 付録B: config.example.toml

```toml
[platform]
obs_flavor = "native"                    # native | flatpak（Linux 実装が解釈する）

[obs]
config_dir  = "~/.config/obs-studio"      # Flatpak 版: ~/.var/app/com.obsproject.Studio/config/obs-studio
launch_cmd  = ["obs"]                     # Flatpak 版: ["flatpak", "run", "com.obsproject.Studio"]
extra_args  = []                          # 例: ["--disable-shutdown-check"]
collection  = ""                          # 空の場合は現在のコレクションを使う
scene       = ""                          # 起動時のシーン（任意）
ready_timeout_s = 30

[obs.websocket]
host = "localhost"
port = 4455
password = ""                             # 空の場合は plugin_config から読む

[target]
source_name = "Camera"
source_uuid = ""                          # 任意。指定した場合はこちらを優先する
filter_name = "Crop/Pad"

[camera]
by_id = ""
by_path = ""
vendor_id = ""
product_id = ""
name = ""
device_id_mode = "resolved"               # resolved | link | none

[camera.format]                           # OBS 側が -1 の場合に使う
width = 1920
height = 1080
fourcc = "MJPG"
fps = 30

[camera.controls]                         # 名前は devices controls で確認する
# focus_automatic_continuous = 0
# focus_absolute = 0
# zoom_absolute = 100
# auto_exposure = 1

[markers]
dictionary = "DICT_4X4_50"
ids = [0, 1, 2, 3]                        # 左上, 右上, 右下, 左下
target_px = 50
quiet_zone_cells = 2

[measure]
warmup_frames = 30
measure_frames = 10
min_valid = 5
margin_px = 0
skew_tol_ratio = 0.01
```
