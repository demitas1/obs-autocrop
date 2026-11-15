# obs-shaderfilter の導入手順（ビルド済みパッケージ）

射影補正の拡張（[PLAN.md](../PLAN.md) 15章、[PLAN_perspective.md](PLAN_perspective.md)）で使う
obs-shaderfilter を、公式リリースのビルド済みパッケージから導入する手順。P0（PLAN.md 12章）の最初の作業。

- 対象: OBS 32.2.0（PPA 版、`/usr/bin/obs`）、Ubuntu 24.04
- 導入先: ユーザーごとのプラグインディレクトリ `~/.config/obs-studio/plugins/`（`sudo` 不要、ディレクトリを消せば元に戻る）
- Flatpak 版の OBS は対象外（プラグインの導入方法が異なる）

## 事前確認の結果（2026-11-15）

| 項目 | 結果 |
|---|---|
| リリース | [2.6.1](https://github.com/exeldro/obs-shaderfilter/releases/tag/2.6.1)（2026-10-24）。対応 OBS は 28 以降、2.6.1 で OBS 33 に対応 |
| Linux 用のファイル | `obs-shaderfilter-2.6.1-ubuntu-22.04.tar.gz`（Ubuntu 22.04 でビルド。.deb はない） |
| SHA256 | `51db19fdf737c236fd7c6583cbcf3690c4b0bf18ad7d6d15610e632839cf687d`（GitHub の digest と一致） |
| 中身 | `obs-shaderfilter/bin/64bit/obs-shaderfilter.so` と `obs-shaderfilter/data/`（locale、examples、textures など 190 ファイル）。ユーザーごとのプラグインディレクトリの形式そのまま |
| 依存ライブラリ | 当環境で `ldd` の結果に `not found` なし。`libobs.so.0` は OBS 32.2.0 のものに解決される。必要な glibc は 2.34 まで（当環境は 2.39） |
| 参考 | 同梱の `data/examples/perspective.shader` は角度指定の 3D 回転で、任意の射影行列は渡せない。本プロジェクトでは別途シェーダーを作る |

注意: Ubuntu 22.04 向けのビルドを 24.04 の OBS 32 で使うため、ABI の互換性は起動して確認するまで分からない。
読み込みに失敗した場合は、ソースからのビルド（`~/work/external/obs-shaderfilter`）を検討する。

## 手順

**OBS を終了した状態で行う**（プラグインは起動時に読み込まれる）。

### 1. ダウンロードと検証

```sh
mkdir -p ~/Downloads/obs-shaderfilter && cd ~/Downloads/obs-shaderfilter
curl -LO https://github.com/exeldro/obs-shaderfilter/releases/download/2.6.1/obs-shaderfilter-2.6.1-ubuntu-22.04.tar.gz
echo "51db19fdf737c236fd7c6583cbcf3690c4b0bf18ad7d6d15610e632839cf687d  obs-shaderfilter-2.6.1-ubuntu-22.04.tar.gz" | sha256sum -c
```

`OK` と表示されること。

### 2. 展開（導入）

```sh
mkdir -p ~/.config/obs-studio/plugins
tar -xzf obs-shaderfilter-2.6.1-ubuntu-22.04.tar.gz -C ~/.config/obs-studio/plugins/
ls ~/.config/obs-studio/plugins/obs-shaderfilter/bin/64bit/
```

`obs-shaderfilter.so` が表示されること。導入後の構成:

```
~/.config/obs-studio/plugins/obs-shaderfilter/
├── bin/64bit/obs-shaderfilter.so
└── data/{manifest.json, locale/, internal/, examples/, textures/}
```

### 3. OBS を起動して読み込みを確認

1. OBS を起動する。
2. ログで読み込みを確認する（最新のログファイル）。

   ```sh
   grep -n -i "shaderfilter" "$(ls -t ~/.config/obs-studio/logs/*.txt | head -1)"
   ```

   - 読み込めた場合: ログの `Loaded Modules:` の一覧に `obs-shaderfilter.so` が出る（`obs-filters.so` などと同じ形式）。
   - 失敗した場合: `Module '.../obs-shaderfilter.so' not loaded` などのエラーが出る（その行を記録する）。
3. OBS のメニュー「ツール → プラグインマネージャー」に obs-shaderfilter が表示されることを確認する（OBS 32 の機能）。
4. 任意のソースを右クリック →「フィルタ」→「+」のメニューに **User-defined shader**（日本語化されていれば相当する名前）があることを確認する。**確認したら追加せずに閉じる**（カメラのソースにはまだ追加しない）。

## 元に戻す（アンインストール）

OBS を終了してから:

```sh
rm -rf ~/.config/obs-studio/plugins/obs-shaderfilter
```

シーンコレクションに User-defined shader のフィルタを追加していた場合は、先に OBS の画面でそのフィルタを削除しておく
（プラグインがない状態で起動すると、そのフィルタは読み込めないものとして扱われる）。

## 結果（2026-11-15）

- 手順どおりに導入し、OBS 32.2.0 で読み込めた（ログ `[obs-shaderfilter] loaded version 2.6.1`、`Loaded Modules:` に `obs-shaderfilter.so`）。
- プラグインマネージャーに表示され、有効。フィルタの追加メニューに「User-defined shader」が出る。
- ソースからのビルドは不要だった。

## 記録

結果（読み込めたか、ログの該当行、プラグインマネージャーの表示、フィルタの名前）は PLAN.md 13章に記録する。
続く P0 の検査（設定の JSON のキー、uniform とプロパティの対応、websocket から設定するキー名）は PLAN.md 12章を参照。
