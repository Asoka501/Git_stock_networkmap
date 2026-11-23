# Git_stock_networkmap

EDINETで開示される有価証券報告書（有報）から企業間の関係を抽出し、3Dネットワークグラフで可視化するプロジェクトです。

主要な顧客・取引先・資本関係といった「企業のつながり」を、球体のノードと関係の強さで太さが変わるエッジで描き、セクターの色分けや企業詳細のパネルとあわせて直感的に眺められるようにします。まずは主要な日本企業24社のサンプルデータで全体の動きを確認できる状態になっており、実データへの差し替えはJSONを入れ替えるだけで行えます。

## 公開URL

https://asoka501.github.io/Git_stock_networkmap/

## 構成

| パス | 内容 |
| --- | --- |
| `index.html` | 画面の骨格（ヘッダー、コントロールパネル、詳細パネル、JSON読込モーダル） |
| `src/main.ts` | 3Dグラフ本体（3d-force-graph / three.js）とUIロジック |
| `src/style.css` | Tailwind CSS とガラス調パネルなどの共通スタイル |
| `src/data/sample.json` | 主要日本企業24社のサンプルネットワークデータ |
| `python/edinet_client.py` | EDINET API v2 から書類一覧とXBRLを取得するクライアント |
| `python/parse_xbrl.py` | 有報XBRLから対象セクション本文と主要な顧客関係を抽出する雛形 |
| `python/build_network.py` | 抽出結果をフロントエンド用ネットワークJSONへ整形する |
| `pages-workflow.yml` | GitHub Pages 公開用のワークフロー定義 |

技術スタック: Vite + TypeScript + Tailwind CSS + Lucide（土台）、three.js ベースの 3d-force-graph（3D描画）。

## データフロー

```
EDINET API v2
  │  書類一覧 documents.json / XBRL(ZIP) documents/{docID}
  ▼
python/edinet_client.py     … 一覧取得・XBRLダウンロード
  ▼
python/parse_xbrl.py        … セクション本文と「主要な顧客」関係を抽出
  ▼
python/build_network.py     … ネットワークJSON（nodes / links）へ整形
  ▼
フロントエンド（3D表示）     … JSONを読み込み、3Dネットワークとして描画
```

フロントエンドは起動時に `src/data/sample.json` を表示します。実データで整形したJSONは、画面右上の「JSON読込」から貼り付けて即座に再描画することもできます。

## ネットワークJSONスキーマ

フロントエンドとPython側の間の契約です。この形式に従うJSONであれば、そのまま3D表示に差し替えられます。

### nodes（企業）

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `id` | string | ノードの一意識別子。証券コードと同値にする |
| `code` | string | 証券コード（4桁） |
| `name` | string | 企業名 |
| `sector` | string | セクター名（色分けの単位。例: 自動車・モビリティ、半導体・電機、商社・エネルギー、通信・IT、金融、素材・重工） |
| `marketCap` | number | 時価総額（兆円）。ノードサイズ切替の基準のひとつ |
| `capital` | number | 資本金（兆円）。ノードサイズ切替の基準のひとつ |
| `logo` | string | ロゴ画像URL。`https://logoapi.sh/ticker/{code}.svg` 形式 |
| `logoText` | string（任意） | ロゴが取得できない場合の代替表示（英字略称） |
| `excerpt` | string（任意） | 詳細パネルに表示する有報テキストの抜粋 |

### links（企業間の関係）

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `source` | string | 関係元のノード `id` |
| `target` | string | 関係先のノード `id` |
| `value` | number | 関係値 0〜100。線の太さと粒子アニメーションの量を決める |
| `kind` | string | 関係の種別（例: 主要顧客・供給、資本・業務提携、金融・融資、共同開発・DX、持ち合い） |
| `detail` | string（任意） | 関係の補足説明。詳細パネルとツールチップに表示する |

### 例

```json
{
  "nodes": [
    {
      "id": "7203",
      "code": "7203",
      "name": "トヨタ自動車",
      "sector": "自動車・モビリティ",
      "marketCap": 48.0,
      "capital": 0.64,
      "logo": "https://logoapi.sh/ticker/7203.svg"
    }
  ],
  "links": [
    { "source": "7203", "target": "6902", "value": 98, "kind": "主要顧客・供給" }
  ]
}
```

## ローカル開発

```bash
npm install
npm run dev      # 開発サーバー
npm run build    # 型チェック＋本番ビルド（dist/ へ出力）
npm run preview  # ビルド結果の確認
```

## Python側の使い方

事前にEDINETでSubscription-Keyを発行し、環境変数に設定します。キーをコードやリポジトリに書き込むことはありません。

```bash
pip install -r python/requirements.txt
export EDINET_API_KEY="<発行したSubscription-Key>"

# 指定日に提出された有価証券報告書の一覧
python python/edinet_client.py list --date 2025-06-27 --out documents.json

# 書類のXBRL（ZIP）をダウンロード（data/raw/ 配下へ）
python python/edinet_client.py download --doc-id S100XXXX --out-dir data/raw

# XBRLからセクション本文と主要な顧客関係を抽出
python python/parse_xbrl.py --zip data/raw/S100XXXX.zip --out extracted.json

# 抽出結果をネットワークJSONへ整形
python python/build_network.py --input extracted.json --out network.json

# サンプルデータのネットワークJSONを出力（動作確認用）
python python/build_network.py --sample --out network.json
```

## 格納方針

- コードと整形済みJSONは、このリポジトリ（Git）を正本として管理します。
- 生のXBRL（ZIP）はリポジトリに入れません（`.gitignore` 済み）。原本はGoogle Driveへバックアップする想定です。
- 企業マスタや抽出途中の一覧は、将来的にスプレッドシートを中間マスタとして併用する余地がありますが、最終的な受け渡し形式は上記のネットワークJSONに統一します。

## 埋め込み（Museアプリ向け）

公開ページは `iframe` でそのまま埋め込めます。`?embed=1` を付けると、ヘッダーと設定パネルを折りたたんだスリムUIで表示されます。

```html
<iframe
  src="https://asoka501.github.io/Git_stock_networkmap/?embed=1"
  width="100%"
  height="600"
  style="border:0;border-radius:12px;"
  loading="lazy"
  allow="fullscreen"
  title="日本株 企業関係 3Dネットワーク">
</iframe>
```

保有銘柄ハイライトや初期フォーカスも、任意のURLパラメータだけで連携できます（例：`?embed=1&holdings=7203,6758,8035&focus=7203&hops=1`）。詳細は [docs/EMBED_UI_SPEC.md](docs/EMBED_UI_SPEC.md) および [docs/SPEC.md](docs/SPEC.md) を参照してください。

## 画面の機能

- `?embed=1` スリム埋め込みモード（折りたたみフィルタドロワー、全画面切替、`ResizeObserver` 追従）
- URLパラメータ連携（`holdings` 保有銘柄ゴールド強調、`focus` 初期フォーカス、`hops` 1〜2ホップ周辺表示）
- セクター色分け・関係種別チップフィルタ・出典フィルタ（定型表 / LLM）
- ノードサイズ切替（時価総額／資本金）と関係値スライダー
- 関係値で変わる線の太さと、関係方向への粒子アニメーション
- ドラッグ回転・スクロールズーム、自動回転のON/OFF、全体表示リセット、ショートカット（`/` 検索、`R` リセット、`Esc` 解除）
- ズーム時に銘柄ロゴとラベルを表示（未取得ロゴの高速フォールバック＆大規模グラフ向けLOD上限制御）
- ノードクリックで関連企業を強調し、詳細パネル（「この銘柄周辺に絞る」「さらに1ホップ広げる」対応）を表示
- 全社横断検索、埋め込みコード生成モーダル、JSON読込で実データへの差し替え

## 今後の課題

- 実APIキーでの大量取得: 提出日を走査して有報をまとめて取得するバッチ化と、取得済み管理。
- 関係抽出の精度向上: `parse_xbrl.py` の簡易抽出を、タクソノミ要素と表構造に基づく抽出へ置き換える。相手企業名の証券コードへの名寄せも含む。
- 時価総額などの外部データ突合せ: `marketCap` / `capital` を企業マスタや外部データと突き合わせて実値化する。
- 関係種別の拡張: 主要な顧客に加えて、持ち合い（政策保有株式）や主要な借入先などの抽出。
