"""有価証券報告書 XBRL のパース（雛形）。

EDINETから取得したXBRL ZIPを展開し、次の情報を抽出する想定のスケルトン。

- 対象セクションの本文テキスト
  - 「主要な顧客ごとの情報」
  - 「事業等のリスク」
  - 「経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析」等
- 「主要な顧客ごとの情報」から読み取れる関係（相手企業名・金額・割合）

実データでの精度向上（タクソノミ要素の網羅、表組みの解釈、企業名の正規化）は
今後の実装課題。TODO コメントの箇所が差し替えポイント。

使い方:
    python parse_xbrl.py --zip data/raw/S100XXXX.zip --out extracted.json
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

# 抽出対象セクションの見出しキーワード
TARGET_SECTIONS = [
    "主要な顧客ごとの情報",
    "事業等のリスク",
    "経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析",
    "主要な経営指標等の推移",
]

# 「主要な顧客」表の見出しとして現れやすい語
CUSTOMER_TABLE_HINTS = ("主要な顧客", "相手先", "販売実績")


@dataclass
class CustomerRelation:
    """「主要な顧客ごとの情報」から抽出した1件の関係。"""

    counterparty: str          # 相手企業名（原文表記）
    amount_million_yen: float | None = None  # 売上高などの金額（百万円）
    ratio_percent: float | None = None       # 総売上等に対する割合（%）
    note: str = ""


@dataclass
class ExtractedFiling:
    """1書類分の抽出結果。build_network.py への入力になる。"""

    doc_id: str
    filer_name: str = ""        # 提出者名（会社名）
    edinet_code: str = ""
    securities_code: str = ""   # 証券コード（4桁）
    sections: dict[str, str] = field(default_factory=dict)
    customers: list[CustomerRelation] = field(default_factory=list)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_text_blocks(xbrl_xml: bytes):
    """インスタンス文書から (要素名, テキスト) を順に返す。

    EDINETの有報XBRLは iXBRL（HTML埋め込み）とプレーンXBRLの2形式がある。
    TODO: 対象ZIP内のファイル種別（PublicDoc配下の .htm / .xbrl）ごとに
    パーサを分け、ix:nonNumeric / ix:nonFraction の name 属性で
    タクソノミ要素名を引けるようにする。
    """
    root = ET.fromstring(xbrl_xml)
    for elem in root.iter():
        text = "".join(elem.itertext()).strip()
        if text:
            yield _localname(elem.tag), text


def extract_sections(blocks: list[tuple[str, str]]) -> dict[str, str]:
    """テキストブロック列から対象セクションの本文を切り出す（簡易版）。

    TODO: 実際の有報は見出し要素（jpcrp_cor:XXXTextBlock 等のTextBlock要素）に
    セクション全文が入るため、要素名→セクション名の対応表を用意し、
    TextBlock単位で正確に取得する方式へ置き換える。
    """
    joined = "\n".join(text for _, text in blocks)
    sections: dict[str, str] = {}
    for heading in TARGET_SECTIONS:
        idx = joined.find(heading)
        if idx >= 0:
            # 見出し位置から次の対象見出し（または末尾）までを本文とする簡易切り出し
            ends = [joined.find(h, idx + len(heading)) for h in TARGET_SECTIONS]
            ends = [e for e in ends if e > idx]
            end = min(ends) if ends else len(joined)
            sections[heading] = joined[idx:end].strip()
    return sections


_AMOUNT_RE = re.compile(r"([\d,]+)\s*(百万円|億円|千円)")
_RATIO_RE = re.compile(r"([\d.]+)\s*[%％]")


def extract_customers(sections: dict[str, str]) -> list[CustomerRelation]:
    """「主要な顧客ごとの情報」本文から相手企業・金額・割合を抽出する（簡易版）。

    TODO: 実データでは表（行=顧客、列=金額・割合）として構造化されているため、
    TextBlock内のHTMLテーブルを行単位で解析する方式に置き換える。
    また相手企業名は証券コードへの名寄せ（企業マスタとの突合せ）が必要。
    """
    text = sections.get("主要な顧客ごとの情報", "")
    if not text:
        return []
    relations: list[CustomerRelation] = []
    # 簡易版: 「社名 … 金額 … 割合」が1行に並ぶ前提で行単位に走査する
    for line in text.splitlines():
        if not any(hint in line for hint in ("株式会社", "合同会社", " Inc", " Corp")):
            continue
        amount_match = _AMOUNT_RE.search(line)
        ratio_match = _RATIO_RE.search(line)
        name_match = re.search(r"([\u4e00-\u9fffA-Za-z0-9・ ]*(?:株式会社|合同会社)[^ \d]*)", line)
        if not name_match:
            continue
        amount = None
        if amount_match:
            value = float(amount_match.group(1).replace(",", ""))
            unit = amount_match.group(2)
            amount = value * {"百万円": 1, "億円": 100, "千円": 0.001}[unit]
        relations.append(
            CustomerRelation(
                counterparty=name_match.group(1).strip(),
                amount_million_yen=amount,
                ratio_percent=float(ratio_match.group(1)) if ratio_match else None,
                note="簡易抽出（要検証）",
            )
        )
    return relations


def parse_xbrl_zip(zip_path: Path) -> ExtractedFiling:
    """XBRL ZIPを読み、ExtractedFilingを返す。"""
    doc_id = zip_path.stem
    blocks: list[tuple[str, str]] = []
    with zipfile.ZipFile(zip_path) as zf:
        # TODO: XBRL/PublicDoc 配下の本体ファイルだけを対象に絞る
        for name in zf.namelist():
            if name.lower().endswith((".xbrl", ".htm", ".html", ".xml")):
                try:
                    blocks.extend(_iter_text_blocks(zf.read(name)))
                except ET.ParseError:
                    continue
    sections = extract_sections(blocks)
    return ExtractedFiling(
        doc_id=doc_id,
        # TODO: ヘッダ要素（jpcrp_cor:CompanyNameCoverPage 等）から提出者名・
        # EDINETコード・証券コードを取得する
        sections=sections,
        customers=extract_customers(sections),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="有価証券報告書 XBRL のパース（雛形）")
    parser.add_argument("--zip", required=True, type=Path, help="XBRL ZIPのパス")
    parser.add_argument("--out", type=Path, default=None, help="抽出結果JSONの保存先（省略時は標準出力）")
    args = parser.parse_args(argv)

    result = parse_xbrl_zip(args.zip)
    text = json.dumps(asdict(result), ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
