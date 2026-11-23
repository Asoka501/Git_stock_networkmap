"""Phase 3 EDINET パイプラインの自動テスト（APIキー不要・完全オフライン実行）。

以下を一気通貫で検証する：
- 企業名の正規化・信託口除外・証券コード名寄せ（CompanyResolver）
- リアルな日本語 iXBRL ZIP のパース（主要な顧客・政策保有株式・大株主・本文共起・抜粋）
- ネットワーク統合・重複解決・スコア計算・AI Pro LLM関係マージ（build_from_extracted）
- フロントエンド用分割静的JSON（meta / index / core / network / details/{0-9}.json）の出力契約検証
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "python"))

from build_network import build_from_extracted, write_split_bundle  # noqa: E402
from company_master import (  # noqa: E402
    CompanyRecord,
    CompanyResolver,
    clean_sec_code,
    is_excluded_counterparty,
    normalize_company_name,
)
from market_data import extract_domain  # noqa: E402
from parse_xbrl import parse_zip  # noqa: E402


SAMPLE_IXBRL_HTML = """<!DOCTYPE html>
<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
<head><meta charset="utf-8" /></head>
<body>
  <ix:nonNumeric name="jpdei_cor:FilerNameInJapaneseDEI">株式会社デンソー</ix:nonNumeric>
  <ix:nonNumeric name="jpdei_cor:SecurityCodeDEI">69020</ix:nonNumeric>
  <ix:nonNumeric name="jpdei_cor:EDINETCodeDEI">E01748</ix:nonNumeric>
  <ix:nonNumeric name="jpdei_cor:CurrentPeriodEndDateDEI">2025-03-31</ix:nonNumeric>

  <h2>主要な顧客ごとの情報</h2>
  <p>（単位：百万円）</p>
  <table>
    <tr><th>相手先の名称</th><th>売上高（百万円）</th><th>割合（％）</th></tr>
    <tr><td>トヨタ自動車株式会社（注1）</td><td>3,200,000</td><td>45.2</td></tr>
    <tr><td>本田技研工業㈱</td><td>480,000</td><td>6.8</td></tr>
  </table>

  <h2>特定投資株式の保有状況</h2>
  <p>（単位：百万円）</p>
  <table>
    <tr><th>銘柄</th><th>株式数（株）</th><th>貸借対照表計上額（百万円）</th><th>保有目的</th></tr>
    <tr><td>トヨタ自動車株式会社</td><td>120,000,000</td><td>350,000</td><td>取引関係の維持・強化</td></tr>
    <tr><td>株式会社アイシン</td><td>15,000,000</td><td>80,000</td><td>協業体制の強化</td></tr>
    <tr><td>銘柄数合計</td><td>-</td><td>430,000</td><td>-</td></tr>
  </table>

  <h2>大株主の状況</h2>
  <table>
    <tr><th>氏名又は名称</th><th>所有株式数（千株）</th><th>発行済株式総数に対する割合（％）</th></tr>
    <tr><td>トヨタ自動車株式会社</td><td>600,000</td><td>24.5</td></tr>
    <tr><td>日本マスタートラスト信託銀行株式会社（信託口）</td><td>350,000</td><td>14.1</td></tr>
    <tr><td>株式会社豊田自動織機</td><td>220,000</td><td>8.9</td></tr>
  </table>

  <ix:nonNumeric name="jpcrp_cor:BusinessRisksTextBlock">
    次世代車載半導体の安定調達に向け、ルネサスエレクトロニクス株式会社との共同開発および長期供給体制の構築を推進しています。またソニーグループ株式会社と自動運転センサー領域で業務提携を進めています。
  </ix:nonNumeric>
</body>
</html>
"""


def _build_test_master() -> dict[str, CompanyRecord]:
    return {
        "6902": CompanyRecord(
            code="6902",
            name="デンソー",
            sector="自動車・モビリティ",
            sector33="輸送用機器",
            market="プライム",
            edinet_code="E01748",
            capital_trillion=0.19,
            aliases=["株式会社デンソー"],
        ),
        "7203": CompanyRecord(
            code="7203",
            name="トヨタ自動車",
            sector="自動車・モビリティ",
            sector33="輸送用機器",
            market="プライム",
            edinet_code="E02144",
            capital_trillion=0.64,
            aliases=["トヨタ自動車株式会社"],
        ),
        "7267": CompanyRecord(
            code="7267",
            name="本田技研工業",
            sector="自動車・モビリティ",
            sector33="輸送用機器",
            market="プライム",
            edinet_code="E02166",
            capital_trillion=0.09,
            aliases=["ホンダ"],
        ),
        "7259": CompanyRecord(
            code="7259",
            name="アイシン",
            sector="自動車・モビリティ",
            sector33="輸送用機器",
            market="プライム",
            capital_trillion=0.05,
        ),
        "6201": CompanyRecord(
            code="6201",
            name="豊田自動織機",
            sector="自動車・モビリティ",
            sector33="輸送用機器",
            market="プライム",
            capital_trillion=0.08,
        ),
        "6723": CompanyRecord(
            code="6723",
            name="ルネサスエレクトロニクス",
            sector="半導体・電機",
            sector33="電気機器",
            market="プライム",
            capital_trillion=0.15,
        ),
        "6758": CompanyRecord(
            code="6758",
            name="ソニーグループ",
            sector="半導体・電機",
            sector33="電気機器",
            market="プライム",
            capital_trillion=0.88,
        ),
        "8035": CompanyRecord(
            code="8035",
            name="東京エレクトロン",
            sector="半導体・電機",
            sector33="電気機器",
            market="プライム",
            capital_trillion=0.06,
        ),
    }


class TestPipeline(unittest.TestCase):
    def test_company_normalization_and_exclusions(self) -> None:
        self.assertEqual(clean_sec_code("72030"), "7203")
        self.assertEqual(clean_sec_code("130A"), "130A")
        self.assertEqual(clean_sec_code(7203.0), "7203")
        self.assertEqual(normalize_company_name("トヨタ自動車株式会社（注1）"), "トヨタ自動車")
        self.assertEqual(normalize_company_name("本田技研工業㈱"), "本田技研工業")
        self.assertTrue(is_excluded_counterparty("日本マスタートラスト信託銀行株式会社（信託口）"))
        self.assertTrue(is_excluded_counterparty("デンソー従業員持株会"))
        self.assertFalse(is_excluded_counterparty("トヨタ自動車株式会社"))
        self.assertEqual(extract_domain("https://www.toyota.co.jp/jpn/"), "toyota.co.jp")

    def test_parse_ixbrl_zip_and_build_split_bundle(self) -> None:
        master = _build_test_master()
        resolver = CompanyResolver(master, alias_overrides={"Honda Motor": "7267"})
        self.assertEqual(resolver.resolve("Honda Motor"), "7267")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "S100TEST.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("XBRL/PublicDoc/jpcrp030000-asr-001.htm", SAMPLE_IXBRL_HTML.encode("utf-8"))

            filing = parse_zip(zip_path, resolver=resolver)
            self.assertEqual(filing.securities_code, "6902")
            self.assertEqual(filing.edinet_code, "E01748")
            self.assertEqual(filing.fiscal_year, "2025-03")

            # 定型表① 主要顧客
            self.assertEqual(len(filing.customers), 2)
            self.assertEqual(filing.customers[0].ratio_percent, 45.2)

            # 定型表② 政策保有株式（銘柄数合計行は除外される）
            self.assertEqual(len(filing.cross_shareholdings), 2)

            # 定型表③ 大株主（日本マスタートラスト信託銀行は自動除外）
            sh_names = [s.shareholder_name for s in filing.major_shareholders]
            self.assertEqual(len(sh_names), 2)
            self.assertFalse(any("マスタートラスト" in n for n in sh_names))

            # ローカル本文共起抽出（ルネサス・ソニーグループを検出）
            text_codes = {t.counterparty_code for t in filing.text_relations}
            self.assertIn("6723", text_codes)
            self.assertIn("6758", text_codes)

            # ネットワーク統合＋分割JSON出力
            market_cache = {
                "7203": {"marketCap": 48.0, "domain": "toyota.co.jp"},
                "6902": {"marketCap": 7.5, "domain": "denso.com"},
            }
            llm_relations = [
                {
                    "source": "6723",
                    "target": "8035",
                    "value": 72,
                    "kind": "資本・業務提携",
                    "detail": "AI Pro解析: 次世代半導体製造装置で協業",
                }
            ]

            network, details_map = build_from_extracted(
                [asdict(filing)],
                master=master,
                market_cache=market_cache,
                llm_relations=llm_relations,
                unmatched_out=tmp_path / "unmatched.json",
            )

            node_ids = {n["id"] for n in network["nodes"]}
            self.assertTrue({"6902", "7203", "7267", "7259", "6201", "6723", "6758", "8035"}.issubset(node_ids))

            toyota = next(n for n in network["nodes"] if n["id"] == "7203")
            self.assertEqual(toyota["marketCap"], 48.0)
            self.assertEqual(toyota["domain"], "toyota.co.jp")
            self.assertEqual(toyota["market"], "プライム")

            link_kinds = {l["kind"] for l in network["links"]}
            self.assertIn("主要顧客・供給", link_kinds)
            self.assertIn("政策保有株式", link_kinds)
            self.assertIn("大株主", link_kinds)
            self.assertIn("資本・業務提携", link_kinds)

            source_types = {l["sourceType"] for l in network["links"]}
            self.assertEqual(source_types, {"table", "llm"})

            # 分割JSONバンドル書き出しとスキーマ確認
            split_dir = tmp_path / "public_data"
            meta = write_split_bundle(
                network,
                details_map,
                split_dir,
                master=master,
                market_cache=market_cache,
                core_max_nodes=5,
            )
            self.assertEqual(meta["dataType"], "live")
            self.assertEqual(meta["coreNodeCount"], 5)
            self.assertTrue((split_dir / "meta.json").exists())
            self.assertTrue((split_dir / "index.json").exists())
            self.assertTrue((split_dir / "core.json").exists())
            self.assertTrue((split_dir / "network.json").exists())
            self.assertTrue((split_dir / "details" / "6.json").exists())

            detail_6 = json.loads((split_dir / "details" / "6.json").read_text(encoding="utf-8"))
            self.assertIn("6902", detail_6)
            self.assertEqual(detail_6["6902"]["docId"], "S100TEST")


if __name__ == "__main__":
    unittest.main()
