# Git_stock_networkmap

EDINETで開示される有価証券報告書（有報）から日本の上場企業間のつながり（政策保有株式・大株主・主要顧客・提携・供給網）を自動抽出し、3Dネットワークグラフで可視化するWebアプリケーションです。

全上場企業（約3,800社）を対象に、**LLMや有料APIへ一切依存しない決定論的な自動パイプライン**（EDINET API v2 + iXBRL定型表パーサ + ローカル本文共起抽出 + `yfinance`）と GitHub Actions / GitHub Pages の無料枠だけで完結・自動運用できるよう設計されています。

## 公開URL

- 本番サイト： https://asoka501.github.io/Git_stock_networkmap/
- コンパクト表示モード（スリムUI）： https://asoka501.github.io/Git_stock_networkmap/?embed=1

## 収録データ実績（全決算期12ヶ月バックフィル済み）

| 項目 | 実績値 | 補足 |
| --- | --- | --- |
| 対象期間 | `2025-12-01` 〜 `2026-11-23`（以降、毎週自動差分更新） | 全決算期（3月・12月・2月・その他決算） |
| パース済み有報数 | **3,750 件** | EDINET上場マスタ 3,811社比 98.4% |
| 全上場インデックス社数 | **3,856 社** | `public/data/index.json`（全社検索対応） |
| 接続ノード数（企業数） | **3,791 社** | 全上場の 98.3% が関係網へ接続 |
| 関係エッジ総数 | **27,391 本** | 定型表（政策保有・大株主・主要顧客）＋本文共起（一般語誤マッチ除去済み・全6種別） |
| 時価総額 / ロゴドメイン補完率 | **96.4% / 96.6%** | 東証上場銘柄はほぼ100%補完（残余は地方単独上場・上場廃止銘柄等） |
| 初期表示コアグラフ | **180 社 / 1,465 本** | 時価総額上位120社＋構造ハブ上位60社（`public/data/core.json` 425KB・高速初期表示） |

## 設計原則（LLM不要・ランニングコスト0円）

1. **LLMなしで完全自律保守**：定期データ取得・iXBRL解析・企業名名寄せ・時価総額/ドメイン補完・分割JSON生成・Pages公開まで、すべて決定論的なPythonコードと無料データソースのみで完結します。外部LLM APIキー（`GEMINI_API_KEY` 等）は一切不要です。
2. **完全無料インフラ**：GitHub Pages（静的配信）＋ GitHub Actions（週次自動バッチ＆手動バックフィル）＋ 無料API（EDINET API v2 / `yfinance` / `logoapi.sh` / LogoKit無料枠）のみでランニングコスト0円を維持します。
3. **全上場規模でも軽快**：配信JSONを `meta` / `index` / `core` / `network` / `details/{0-9}` に分割し、初回は主要ハブ180社のみを描画、検索や範囲切替時に全社データを遅延読込します。

## リポジトリ構成

| パス | 内容 |
| --- | --- |
| `index.html` | 画面レイアウト（検索バー、フィルタドロワー、企業詳細インスペクタ、共有URL/JSON読込モーダル） |
| `src/main.ts` | 3Dグラフ描画（`3d-force-graph` / `three.js`）、ズーム連動LOD、フィルタ、インスペクタUI |
| `src/dataLoader.ts` | 分割静的JSON（`meta` / `index` / `core` / `network` / `details`）の段階読込＆サンプルフォールバック |
| `src/sectorPalette.ts` | 11大分類カラーパレット・ズームイン時33業種近似色・業種表記揺れ正規化 |
| `src/logoLoader.ts` | 企業ロゴ多段フォールバック（`logoapi.sh` → LogoKit → 略称バッジ）とインスペクタミニロゴ遅延読込 |
| `src/graphUtils.ts` | 1〜2ホップ部分グラフ抽出・保有銘柄周辺スコープ計算 |
| `src/embedConfig.ts` | URLクエリパラメータ（`?embed=1`, `focus`, `holdings`, `hops`, `mode` 等）の解析 |
| `src/types.ts` | フロントエンド共通型定義（ノード・エッジ・詳細バケット・URL設定） |
| `src/data/sample.json` | 開発・フォールバック用の24社サンプルデータ |
| `python/run_pipeline.py` | 全上場データ更新パイプラインの統合エントリポイント |
| `python/company_master.py` | EDINETコードリスト（`Edinetcode.zip` 主系）＋ JPX一覧（任意補助）による企業マスタ＆11セクター分類 |
| `python/edinet_client.py` | EDINET API v2 書類一覧走査・有報ZIP差分ダウンロード（`docId` マニフェスト管理） |
| `python/parse_xbrl.py` | iXBRL定型表パーサ（`colspan`/`rowspan` 展開・列単位の通貨単位判定）＋ ローカル本文共起抽出 |
| `python/market_data.py` | `yfinance` による時価総額（兆円）・公式サイトドメインのハブ優先TTLキャッシュ更新 |
| `python/build_network.py` | 企業名名寄せ・異常額ガード・スコア統合・分割JSON（`public/data/`）生成 |
| `python/ops_helper.py` | 名寄せ例外辞書・未一致ログ集計・運用管理シート連携用ヘルパー |
| `python/check_api.py` | EDINET API v2 疎通確認スクリプト |
| `python/tests/test_pipeline.py` | APIキー不要のオフライン単体テスト一式 |
| `data/alias_overrides.json` | 営業子会社・略称を親会社証券コードへ紐づける名寄せ例外辞書（人間が保守） |
| `data/llm_relations.json` | 任意拡張用の手動／追加関係データ格納ファイル（空配列 `[]` のままでも完全稼働） |
| `public/data/` | GitHub Pages へ配信される生成済み分割JSON一式（Actionsが自動更新） |
| `.github/workflows/update-data.yml` | 毎週日曜の自動差分更新・手動バックフィル用ワークフロー |
| `.github/workflows/pages.yml` | `main` 更新および `update-data.yml` 完了連動での GitHub Pages 自動デプロイ |

技術スタック：Vite + TypeScript + Tailwind CSS + Lucide + `3d-force-graph`（three.js）／ Python 3.12（`requests`, `beautifulsoup4`, `lxml`, `yfinance`）。

## データフロー

```text
EDINETコードリスト (Edinetcode.zip) + data/alias_overrides.json
  │
  ▼
EDINET API v2 (documents.json / documents/{docID}?type=1)
  │  python/edinet_client.py   … docIdマニフェストによる未処理有報の差分ZIP取得
  ▼
python/parse_xbrl.py           … iXBRL定型表パース（政策保有・大株主・主要顧客）
                                 ＋ ローカルPython企業名共起抽出（LLM不要）
  ▼
python/market_data.py          … yfinance で時価総額＆公式サイトドメインをハブ優先補完
  ▼
python/build_network.py        … 名寄せ・異常額ガード・スコア統合 → public/data/ 分割JSON出力
  ▼
GitHub Pages（自動デプロイ）    … 3Dネットワークビューアで配信
```

## 主な画面機能

- **11大分類カラーパレット＋ズーム連動33業種近似色**：俯瞰時は人間が識別しやすい11カテゴリ配色、ズームイン時はJPX 33業種ごとの近似色へ自動切替
- **多段企業ロゴ表示**：3Dノード上（ズーム連動LOD＋描画数上限制御）および詳細インスペクタの関係企業一覧にミニロゴ（`20×20px` 遅延読込）を表示。`logoapi.sh/{domain}` → LogoKit → セクター色略称バッジへ自動フォールバック
- **表示スコープ切替**：コアハブ（上位180社）／保有銘柄周辺／選択企業の1〜2ホップ展開／全社表示
- **多彩なフィルタ＆検索**：全3,856社インクリメンタル検索、11セクターON/OFF、関係種別6種（政策保有株式・大株主・主要顧客・資本業務提携・共同開発・供給網リスク）、出典フィルタ（定型表のみ / 本文抽出含む）、ノードサイズ切替（時価総額 / 資本金）、関係値スライダー
- **URLクエリ連携＆スリム表示**：`?embed=1`（スリムUI）、`?focus=7203`、`?holdings=7203,6758,8035`、`?hops=1` などで任意の状態をURL共有可能
- **操作ショートカット**：`/` 検索フォーカス、`R` 全体表示リセット、`Esc` 選択・モーダル解除

## ローカル開発・テスト

```bash
# フロントエンド
npm install
npm run dev      # 開発サーバー
npm run build    # 型チェック（tsc --noEmit）＋ 本番ビルド（dist/）
npm run preview  # ビルド結果プレビュー

# Pythonパイプライン単体テスト（APIキー不要・オフライン実行可）
python3 -m venv .venv
.venv/bin/pip install -r python/requirements.txt
.venv/bin/python -m unittest discover -s python/tests -v
```

## パイプライン手動実行・保守コマンド

事前にEDINET閲覧サイトで無料の `Subscription-Key` を発行し、環境変数（自動更新時は GitHub Actions Repository Secrets の `EDINET_API_KEY`）へ設定します。

```bash
export EDINET_API_KEY="<発行したSubscription-Key>"

# 1. EDINET API v2 疎通確認
.venv/bin/python python/check_api.py

# 2. 直近14日分の通常差分更新
.venv/bin/python python/run_pipeline.py --days 14 --max-downloads 120 --market-cap-limit 300 --out-dir public/data

# 3. 期間指定バックフィル（例：6月決算集中期）
.venv/bin/python python/run_pipeline.py --from 2026-06-01 --to 2026-06-30 --max-downloads 600 --market-cap-limit 600 --out-dir public/data

# 4. 時価総額・ドメインのみのキャッシュ補完（有報ダウンロード0件）
.venv/bin/python python/run_pipeline.py --days 3 --max-downloads 0 --market-cap-limit 1000 --out-dir public/data
```

### GitHub Actions での保守運用（LLM不要）

- **定期更新**：[.github/workflows/update-data.yml](.github/workflows/update-data.yml) が毎週日曜（UTC 18:00）に自動実行され、新着有報と期限切れ時価総額を差分更新して `public/data/` をコミットします。
- **本番自動反映**：`update-data.yml` の成功完了をトリガー（`workflow_run`）に [.github/workflows/pages.yml](.github/workflows/pages.yml) が自動実行され、GitHub Pages へ即座に反映されます。
- **名寄せ精度の改善**：未一致の企業名（例：営業子会社や略称）を親会社コードへ紐づけたい場合は、[data/alias_overrides.json](data/alias_overrides.json) に `"表記": "4桁コード"` を1行追記してPRマージするだけで次回実行から自動反映されます。

## 関連ドキュメント

- [docs/SPEC.md](docs/SPEC.md) — システム全体仕様・設計原則・アーカイブ済みフェーズ実績・セキュリティ運用
- [docs/EMBED_UI_SPEC.md](docs/EMBED_UI_SPEC.md) — フロントエンドUI/UX・11セクター配色・ロゴ多段フォールバック・URLクエリ仕様
- [docs/DATA_SCHEMA_AND_PIPELINE.md](docs/DATA_SCHEMA_AND_PIPELINE.md) — 分割JSONスキーマ・EDINET自動パイプライン詳細・LLM非依存の長期保守手順
