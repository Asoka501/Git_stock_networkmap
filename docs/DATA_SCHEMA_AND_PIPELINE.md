# データスキーマ・EDINET自動パイプライン＆保守運用仕様書

## 1. 配信用分割JSON設計（GitHub Pages 静的配信）

全上場企業（約3,800社）を無料かつ高速に配信するため、静的JSONを役割別に分割して `public/data/` へ出力する。単一JSON（`src/data/sample.json`）スキーマとも後方互換性を維持している。

| ファイルパス | 実測サイズ（非圧縮） | 内容・用途（3,750有報バックフィル後） | 読込タイミング |
| --- | --- | --- | --- |
| `public/data/meta.json` | 0.2 KB | 最終更新日時、収録企業数（3,791）、インデックス数（3,856）、エッジ数（27,391）、データ種別（`live`） | 起動時 |
| `public/data/index.json` | 888 KB | 全上場3,856社の軽量検索インデックス（`code`, `name`, `sector`, `subSector`, `market`, `marketCap`, `capital`, `degree`） | 起動時（全社検索用） |
| `public/data/core.json` | 425 KB | 初期表示用コアグラフ（時価総額上位120社＋構造ハブ上位60社のハイブリッド選定 **180社・1,465本**） | 起動時（即座に3D描画） |
| `public/data/network.json` | 8.2 MB | 全接続企業 **3,791社** のノード基本情報＋全関係エッジ **27,391本**（抜粋テキストは分離し軽量化） | コア外検索・全社表示切替時に遅延読込 |
| `public/data/details/{1-9}.json` | 計 2.2 MB（各150〜400KB） | 証券コード先頭1桁で分割した企業詳細（有報抜粋・提出日・`docId`） | ノード選択時に該当バケットのみ読込＆キャッシュ |

> **フォールバック挙動**：`public/data/core.json` が存在しない開発環境や読込エラー時は、自動的に同梱の `src/data/sample.json` へフォールバックする。

---

## 2. ネットワークJSONスキーマ（フロント・バックエンド契約）

### 2.1 `nodes`（企業）

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `id` | string | 必須 | 一意識別子（4桁証券コード。英字入り新コード例 `130A` も文字列保持） |
| `code` | string | 必須 | 証券コード（4桁） |
| `name` | string | 必須 | 企業名（正規化済みの表示名） |
| `sector` | string | 必須 | 11大分類セクター名（[src/sectorPalette.ts](../src/sectorPalette.ts) / [python/company_master.py](../python/company_master.py) 準拠） |
| `subSector` | string | 任意 | JPX 33業種名（ズームイン時の近似色グラデーションに使用） |
| `marketCap` | number | 必須 | 時価総額（兆円。未取得時は `0`） |
| `capital` | number | 必須 | 資本金（兆円。未取得時は `0`） |
| `logo` | string | 必須 | プライマリロゴURL（`https://logoapi.sh/ticker/{code}.svg`） |
| `domain` | string | 任意 | 企業公式サイトドメイン（例：`global.toyota`、`mufg.jp`）。`logoapi.sh/{domain}`・LogoKitフォールバックに使用 |
| `logoFallbacks` | string[] | 任意 | 追加フォールバックロゴURL配列 |
| `logoText` | string | 任意 | ロゴ代替の英字・カナ略称 |
| `market` | string | 任意 | 市場区分（取得可能な場合） |
| `edinetCode` | string | 任意 | EDINETコード（例：`E02144`） |
| `fiscalYear` | string | 任意 | 取得元有報の決算期（例：`2026-03`） |
| `excerpt` | string | 任意 | 有報テキスト要約（本番分割出力時は `details/*.json` へ分離） |

### 2.2 `links`（企業間の関係）

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `source` | string | 必須 | 関係元ノード `id` |
| `target` | string | 必須 | 関係先ノード `id` |
| `value` | number | 必須 | 関係値 `0〜100`（線の太さ・粒子・スライダー基準） |
| `kind` | string | 必須 | 全6種別：`政策保有株式` / `大株主` / `主要顧客・供給` / `資本・業務提携` / `共同開発・協業` / `供給網・リスク` |
| `sourceType` | `"table" \| "llm"` | 任意 | `table`＝有報定型表ルールベース抽出、`llm`＝本文テキスト解析（ローカルPython共起抽出または任意の手動追記データ） |
| `detail` | string | 任意 | 補足テキスト（例：`政策保有株式 貸借対照表計上額 12,345百万円` / `大株主 持株比率 18.5%`） |
| `amountMillionYen` | number | 任意 | 定型表から取得した金額（百万円） |
| `ratioPercent` | number | 任意 | 売上構成比・持株比率（%） |
| `docId` | string | 任意 | 出典のEDINET書類管理番号（例：`S100XXXX`） |

---

## 3. LLM不要・完全自動EDINET抽出パイプライン

すべての定期更新・バックフィル・名寄せ・時価総額更新は、**LLMや有料APIを一切使わず** [python/run_pipeline.py](../python/run_pipeline.py) のみで決定論的に実行される。

### 3.1 処理ステップ

```text
[Step 1] 企業マスタ同期 (python/company_master.py)
  - 主系：金融庁 EDINETコードリスト（Edinetcode.zip / CP932 CSV）を取得
    ※ 上場判定は「listing == "上場" かつ "非上場" not in listing」で厳密判定
  - 補助：JPX上場銘柄一覧（URL変更・404時は非致命エラーとしてスキップ）
  - 33業種→11大分類マッピング（倉庫・運輸関連 / 外国法人・組合 等の表記揺れ吸収）
  - data/alias_overrides.json（営業子会社・略称41件の名寄せ例外辞書）を統合
        │
[Step 2] EDINET API v2 差分取得 (python/edinet_client.py)
  - エンドポイント：https://api.edinet-fsa.go.jp/api/v2
  - 認証：クエリパラメータ Subscription-Key（環境変数 EDINET_API_KEY）
  - 対象期間の提出日を走査し、上場企業の有価証券報告書（docTypeCode="120"）を抽出
  - data/cache/manifest.json と突合し、未処理 docId のみ ZIP ダウンロード（ZIPマジックバイト検証付き）
  - 処理済みZIPはメモリ／一時領域で即破棄（リポジトリへは一切保存しない）
        │
[Step 3] iXBRL定型表パース＋ローカル本文共起抽出 (python/parse_xbrl.py)
  - 定型表パーサ（ルールベース・高精度）：
      ・_expand_table_grid() で colspan / rowspan 結合セルを展開し2段ヘッダーを正確に列対応
      ・列ごとの通貨単位（千円 / 百万円 / 億円）を自動検出し百万円へ正規化
      ・抽出対象：1) 特定投資株式（政策保有株式） 2) 大株主の状況 3) 主要な顧客ごとの情報
  - ローカル本文共起抽出（API不要・完全自動）：
      ・事業等のリスク・経営上の重要な契約・MD&A等から、上場企業名辞書と
        キーワード（「業務提携」「合弁」「共同開発」「供給」「委託」等）の共起を自動抽出
  - 結果は data/cache/extracted/{docId}.json へキャッシュ
        │
[Step 4] 時価総額・公式サイトドメイン補完 (python/market_data.py)
  - ネットワーク次数（接続企業数）上位の主要ハブを最優先して yfinance（{code}.T）で取得
  - 7日TTLキャッシュ（data/cache/market_caps.json）。取得失敗時は既存値を保持し0上書きを防止
  - 全上場3,856社のうち東証上場銘柄をほぼ100%カバー（全体補完率 96.4〜96.6%）
    ※ 残り約3.5%は地方単独上場（名証・福証・札証）および直近の上場廃止・MBO銘柄
        │
[Step 5] 名寄せ・異常額ガード・分割JSON出力 (python/build_network.py)
  - 企業名正規化（全角/半角、株式会社/㈱除去、空白統一）＋ alias_overrides.json 解決
  - 旧キャッシュ対策の異常額ガード（政策保有金額 > 5,000,000百万円＝5兆円超は誤パースとみなして除外）
  - data/llm_relations.json が存在しレコードがある場合のみ任意マージ（空配列 [] でも完全動作）
  - 関係値 value (0〜100) を算出し、public/data/{meta,index,core,network,details/*}.json へ出力
```

### 3.2 関係値（`value`: 0〜100）の算出基準

| 関係種別 | 算出ルール（[python/build_network.py](../python/build_network.py)） |
| --- | --- |
| `主要顧客・供給` | 売上構成比（%）または金額ベースで `20〜100` に正規化 |
| `政策保有株式` | 貸借対照表計上額（百万円）の対数スケールで `25〜95` に正規化（5兆円超は異常値ガード） |
| `大株主` | 持株比率（%）ベースで `25〜100` に正規化 |
| `資本・業務提携` / `共同開発・協業` / `供給網・リスク` | 本文共起スコア（`35〜75`）に上限を設け、定型表の定量関係より過大評価されないよう調整 |

---

## 4. LLM非依存・長期保守運用ガイド

本システムは、**将来LLMや外部AIツールを一切使わない状態でも、通常のGitHub操作とPythonコマンドだけで継続保守できる**よう設計している。

### 4.1 役割分担とコスト

| 基盤 / ファイル | 役割 | コスト | 保守上のポイント |
| --- | --- | --- | --- |
| **GitHub リポジトリ & Pages** | コード・仕様書・配信JSON（`public/data/`）の正本と静的配信 | 無料 | 生ZIP・`data/cache/` は `.gitignore` で除外 |
| **GitHub Actions** | 毎週日曜の自動差分更新（`update-data.yml`）＆ Pages 自動デプロイ（`pages.yml`） | 無料 | `concurrency` 制御で多重実行を防止、`actions/cache@v4` で解析済みキャッシュを継続引き継ぎ |
| **名寄せ例外辞書** | [data/alias_overrides.json](../data/alias_overrides.json)（41件の営業子会社・略称マップ） | 無料 | 人間がJSONへ1行追記→PRマージするだけで即反映 |
| **Google Sheets（任意補助）** | `Git_stock_networkmap 運用管理シート`（`batch_status` 上位200ハブ、`alias_overrides` メモ） | 無料 | 運用状況の可視化補助用。シートがなくても自動バッチ・Webアプリは100%自立稼働 |
| **`data/llm_relations.json`** | 手動や外部調査で特定エッジを追加したい場合のみ使う任意ファイル | 無料 | 空配列 `[]` のままで一切問題なし。保守必須ではない |

### 4.2 必要なシークレット（GitHub Repository Secrets）

- `EDINET_API_KEY`（**必須・無料**）：EDINET閲覧サイトで発行した Subscription-Key。有効期限・失効時は再発行して Secrets を差し替えるだけで復旧する。
- `VITE_LOGOKIT_TOKEN`（**任意・無料枠**）：LogoKit 公開トークン（`pk_...`）。未設定や失効時でも `logoapi.sh/{domain}` → 略称バッジへ自動フォールバックするためサイトは停止しない。

### 4.3 よくある保守作業チェックリスト（LLM不要）

1. **新着有報の手動取り込み／特定期間のバックフィル**
   - GitHub の Actions タブ → `Update EDINET Network Data` → `Run workflow` から `from_date` / `to_date` / `max_downloads`（上限目安 600）/ `market_cap_limit` を指定して実行する。
   - 完了すると `Deploy to GitHub Pages` が `workflow_run` で自動起動し、約1分後に本番反映される。
2. **未名寄せの主要子会社・略称を追加したいとき**
   - [data/alias_overrides.json](../data/alias_overrides.json) に `"有報上の表記": "4桁証券コード"` を追記し、以下でテストしてPRマージする：
     ```bash
     .venv/bin/python -m unittest discover -s python/tests -v
     ```
3. **EDINET API 疎通エラーが出たとき**
   - ローカルで `EDINET_API_KEY=... .venv/bin/python python/check_api.py` を実行し、HTTPステータス・ZIPマジックバイト応答を確認する。
   - 401/403 の場合は `EDINET_API_KEY` の再発行または Secrets 設定箇所（Environments ではなく **Repository secrets**）を確認する。
4. **フロントエンド変更時の検証**
   - `npm run build`（`tsc --noEmit && vite build`）がエラーなく通ることを確認してからPRを作成する。
5. **キャッシュ破損・全面再パースしたいとき**
   - GitHub Actions の Caches 画面から `edinet-cache-*` を削除したうえで、`update-data.yml` を期間分割（1回 `max_downloads=600` 目安・約45分）で再実行する。
