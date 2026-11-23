"""有価証券報告書 iXBRL / XBRL パーサ（定型表＋ローカル本文共起抽出）。

EDINETから取得したXBRL ZIPを展開し、以下を高精度に自動抽出する：
1. 定型表①「主要な顧客ごとの情報」（相手先・売上金額・割合）
2. 定型表②「特定投資株式（政策保有株式）」（銘柄名・株式数・貸借対照表計上額・保有目的）
3. 定型表③「大株主の状況」（法人大株主名・持株比率。信託口・持株会は自動除外）
4. 本文テキスト共起関係（事業等のリスク・重要な契約・MD&A等から、
   企業マスタに一致する上場企業名と関係キーワードの共起文をローカルPythonで自動抽出）
5. 詳細パネル表示用の有報要約抜粋（excerpt）

外部LLM APIキー不要・完全無料で動作する。

使い方:
    python python/parse_xbrl.py --zip data/raw/S100XXXX.zip --out extracted.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, Tag

try:
    from company_master import (
        CompanyResolver,
        clean_sec_code,
        is_excluded_counterparty,
        load_company_master,
    )
except ImportError:
    from python.company_master import (  # type: ignore
        CompanyResolver,
        clean_sec_code,
        is_excluded_counterparty,
        load_company_master,
    )

# 抽出対象セクションの見出しキーワード
TARGET_SECTIONS = [
    "主要な顧客ごとの情報",
    "経営上の重要な契約等",
    "事業等のリスク",
    "経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析",
    "研究開発活動",
]

# ローカル本文共起抽出の関係トリガーキーワード → (関係種別, 基礎スコア)
TEXT_RELATION_RULES: list[tuple[str, tuple[str, ...], int]] = [
    ("資本・業務提携", ("資本業務提携", "資本提携", "業務提携", "合弁会社", "合弁事業", "共同出資"), 68),
    ("共同開発・協業", ("共同開発", "共同研究", "協業", "技術提携", "ライセンス契約", "OEM"), 58),
    ("主要顧客・供給", ("主要顧客", "主要な顧客", "販売代理店", "継続的取引", "長期供給", "調達先", "供給契約"), 55),
    ("供給網・リスク", ("サプライチェーン", "部品調達", "半導体調達", "製造委託", "生産委託", "依存"), 45),
]

_NUM_RE = re.compile(r"([0-9,]+(?:\.[0-9]+)?)")
_SENTENCE_SPLIT_RE = re.compile(r"[。\n]+")


@dataclass
class CustomerRelation:
    """「主要な顧客ごとの情報」から抽出した1件の関係。"""

    counterparty: str
    amount_million_yen: float | None = None
    ratio_percent: float | None = None
    note: str = ""


@dataclass
class ShareholdingRelation:
    """「特定投資株式（政策保有株式）」から抽出した1件の関係。"""

    target_name: str
    shares: int | None = None
    amount_million_yen: float | None = None
    purpose: str = ""


@dataclass
class MajorShareholderRelation:
    """「大株主の状況」から抽出した1件の法人大株主関係。"""

    shareholder_name: str
    ratio_percent: float | None = None
    shares: int | None = None


@dataclass
class TextRelation:
    """本文テキストの企業名共起から抽出した1件の関係（ローカルNLP）。"""

    counterparty_code: str
    counterparty_name: str
    kind: str
    score: int
    snippet: str


@dataclass
class ExtractedFiling:
    """1書類分の抽出結果。build_network.py への入力になる。"""

    doc_id: str
    filer_name: str = ""
    edinet_code: str = ""
    securities_code: str = ""
    fiscal_year: str = ""
    excerpt: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    customers: list[CustomerRelation] = field(default_factory=list)
    cross_shareholdings: list[ShareholdingRelation] = field(default_factory=list)
    major_shareholders: list[MajorShareholderRelation] = field(default_factory=list)
    text_relations: list[TextRelation] = field(default_factory=list)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def _parse_number(text: str) -> float | None:
    cleaned = unicodedata.normalize("NFKC", text or "").replace(",", "").replace("△", "-").replace("▲", "-")
    m = _NUM_RE.search(cleaned)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _detect_unit_multiplier(context_text: str) -> float:
    """金額を百万円へ換算する係数を推定する。"""
    s = unicodedata.normalize("NFKC", context_text or "")
    if "億円" in s:
        return 100.0
    if "百万円" in s:
        return 1.0
    if "千円" in s:
        return 0.001
    if "単位:円" in s.replace(" ", "") or "単位：円" in s.replace(" ", ""):
        return 1e-6
    return 1.0


def _table_rows(table: Tag) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [_norm(td.get_text(" ", strip=True)) for td in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    return rows


def _expand_table_grid(table: Tag) -> list[list[str]]:
    """rowspan / colspan を展開した2次元グリッドを返す（多段ヘッダの列ズレ防止）。"""
    grid: list[list[str]] = []
    spans: dict[tuple[int, int], str] = {}

    for r_idx, tr in enumerate(table.find_all("tr")):
        row: list[str] = []
        c_idx = 0
        for cell in tr.find_all(["th", "td"]):
            while (r_idx, c_idx) in spans:
                row.append(spans.pop((r_idx, c_idx)))
                c_idx += 1

            text = _norm(cell.get_text(" ", strip=True))
            try:
                colspan = max(1, min(12, int(cell.get("colspan") or 1)))
            except ValueError:
                colspan = 1
            try:
                rowspan = max(1, min(12, int(cell.get("rowspan") or 1)))
            except ValueError:
                rowspan = 1

            for dc in range(colspan):
                row.append(text)
                for dr in range(1, rowspan):
                    spans[(r_idx + dr, c_idx + dc)] = text
            c_idx += colspan

        while (r_idx, c_idx) in spans:
            row.append(spans.pop((r_idx, c_idx)))
            c_idx += 1

        if any(row):
            grid.append(row)
    return grid


def _preceding_context_text(tag: Tag, max_chars: int = 300) -> str:
    """テーブル直前の見出しや単位表記（「単位：百万円」等）を取得する。"""
    chunks: list[str] = []
    cur = tag.previous_sibling
    steps = 0
    while cur is not None and steps < 8:
        if isinstance(cur, Tag):
            txt = _norm(cur.get_text(" ", strip=True))
            if txt:
                chunks.append(txt)
                if sum(len(c) for c in chunks) >= max_chars:
                    break
        elif isinstance(cur, str):
            txt = _norm(cur)
            if txt:
                chunks.append(txt)
        cur = cur.previous_sibling
        steps += 1
    return " ".join(reversed(chunks))[-max_chars:]


def _extract_filer_metadata(soups: list[BeautifulSoup]) -> dict[str, str]:
    """iXBRLタグ（ix:nonNumeric）から提出会社名・証券コード・EDINETコード・決算期を取得する。"""
    meta: dict[str, str] = {}
    name_map = {
        "CompanyNameCoverPage": "filer_name",
        "FilerNameInJapaneseDEI": "filer_name",
        "SecurityCodeDEI": "securities_code",
        "EDINETCodeDEI": "edinet_code",
        "CurrentPeriodEndDateDEI": "fiscal_year",
    }
    for soup in soups:
        for tag in soup.find_all(attrs={"name": True}):
            attr_name = str(tag.get("name") or "")
            local = attr_name.split(":")[-1]
            if local in name_map and name_map[local] not in meta:
                val = _norm(tag.get_text(" ", strip=True))
                if val:
                    meta[name_map[local]] = val
        if len(meta) >= 4:
            break
    if "securities_code" in meta:
        meta["securities_code"] = clean_sec_code(meta["securities_code"])
    if "fiscal_year" in meta and len(meta["fiscal_year"]) >= 7:
        meta["fiscal_year"] = meta["fiscal_year"][:7]
    return meta


def extract_customers_from_tables(soups: list[BeautifulSoup]) -> list[CustomerRelation]:
    """iXBRL内の「主要な顧客ごとの情報」テーブルから顧客・金額・割合を抽出する。"""
    relations: list[CustomerRelation] = []
    seen: set[str] = set()

    for soup in soups:
        for table in soup.find_all("table"):
            rows = _expand_table_grid(table)
            if len(rows) < 2:
                continue
            header_text = " ".join(rows[0])
            context = _preceding_context_text(table)
            combined_hint = header_text + " " + context

            is_customer_table = (
                ("相手先" in header_text or "顧客" in header_text)
                and any(k in combined_hint for k in ("売上", "販売", "収益", "営業収益", "主要な顧客"))
                and "貸借対照表" not in combined_hint
                and "所有株式" not in combined_hint
            )
            if not is_customer_table:
                continue

            header = rows[0]
            col_name = next((i for i, h in enumerate(header) if any(k in h for k in ("相手先", "顧客", "名称"))), 0)
            col_amount = next(
                (i for i, h in enumerate(header) if any(k in h for k in ("売上", "金額", "収益", "販売"))),
                None,
            )
            col_ratio = next((i for i, h in enumerate(header) if any(k in h for k in ("割合", "構成比", "%"))), None)
            amount_hint = (header[col_amount] if col_amount is not None else "") + " " + combined_hint
            mult = _detect_unit_multiplier(amount_hint)

            for row in rows[1:]:
                if col_name >= len(row):
                    continue
                name = row[col_name]
                if not name or is_excluded_counterparty(name) or name in seen:
                    continue
                amount_raw = _parse_number(row[col_amount]) if col_amount is not None and col_amount < len(row) else None
                ratio_raw = _parse_number(row[col_ratio]) if col_ratio is not None and col_ratio < len(row) else None
                amount_m = round(amount_raw * mult, 1) if amount_raw is not None else None
                ratio = ratio_raw if (ratio_raw is not None and 0 < ratio_raw <= 100) else None
                if amount_m is None and ratio is None:
                    continue
                seen.add(name)
                parts = []
                if ratio is not None:
                    parts.append(f"売上構成比 {ratio:.1f}%")
                if amount_m is not None:
                    parts.append(f"{amount_m:,.0f}百万円")
                relations.append(
                    CustomerRelation(
                        counterparty=name,
                        amount_million_yen=amount_m,
                        ratio_percent=ratio,
                        note=" / ".join(parts),
                    )
                )
    return relations


def extract_cross_shareholdings(soups: list[BeautifulSoup]) -> list[ShareholdingRelation]:
    """iXBRL内の「特定投資株式（政策保有株式）」テーブルを抽出する（多段ヘッダ・千円/百万円単位対応）。"""
    relations: list[ShareholdingRelation] = []
    seen: set[str] = set()

    for soup in soups:
        for table in soup.find_all("table"):
            rows = _expand_table_grid(table)
            if len(rows) < 2:
                continue
            header_joined = " ".join(rows[0])
            header_two = " ".join(rows[0] + (rows[1] if len(rows) > 1 else []))
            context = _preceding_context_text(table)
            combined = header_two + " " + context

            if not ("銘柄" in header_joined and ("貸借対照表" in combined or "保有目的" in combined or "特定投資株式" in combined)):
                continue

            # 2段ヘッダ（colspan/rowspan展開済み）を列ごとに結合して正確に列特定する
            data_start = 1
            max_cols = max(len(rows[0]), len(rows[1]) if len(rows) > 1 else 0)
            is_two_row_header = len(rows) > 2 and any(
                k in " ".join(rows[1]) for k in ("株式数", "計上額", "貸借対照表", "当事業年度", "百万円", "千円")
            )
            if is_two_row_header:
                data_start = 2
                merged_header = [
                    f"{rows[0][c] if c < len(rows[0]) else ''} {rows[1][c] if c < len(rows[1]) else ''}".strip()
                    for c in range(max_cols)
                ]
            else:
                merged_header = list(rows[0])

            col_name = next((i for i, h in enumerate(merged_header) if "銘柄" in h), 0)
            # 当事業年度の貸借対照表計上額を最優先で取得（前事業年度より左にある最初の列）
            col_amount = next(
                (i for i, h in enumerate(merged_header) if ("貸借対照表" in h or "計上額" in h) and "前事業年度" not in h),
                None,
            )
            if col_amount is None:
                col_amount = next(
                    (i for i, h in enumerate(merged_header) if "貸借対照表" in h or "計上額" in h),
                    None,
                )
            col_shares = next(
                (i for i, h in enumerate(merged_header) if "株式数" in h and "前事業年度" not in h),
                next((i for i, h in enumerate(merged_header) if "株式数" in h), None),
            )
            col_purpose = next((i for i, h in enumerate(merged_header) if "保有目的" in h or "目的" in h), None)

            # 金額列自身の見出し単位（「千円」「百万円」「億円」）を最優先で判定する
            amount_col_header = merged_header[col_amount] if col_amount is not None else ""
            if any(u in amount_col_header for u in ("億円", "百万円", "千円")):
                mult = _detect_unit_multiplier(amount_col_header)
            else:
                mult = _detect_unit_multiplier(combined)

            for row in rows[data_start:]:
                if col_name >= len(row):
                    continue
                target_name = row[col_name]
                if not target_name or is_excluded_counterparty(target_name):
                    continue
                if any(k in target_name for k in ("銘柄数", "合計", "計上額", "該当事項", "銘柄")):
                    continue
                if target_name in seen:
                    continue

                amount_m: float | None = None
                if col_amount is not None and col_amount < len(row):
                    raw_cell = row[col_amount]
                    if "株" not in raw_cell:
                        val = _parse_number(raw_cell)
                        if val is not None and val > 0:
                            scaled = round(val * mult, 1)
                            # 単一政策保有の現実上限（10兆円）を超える異常値（株数誤認等）は除外
                            if scaled <= 10_000_000:
                                amount_m = scaled

                shares: int | None = None
                if col_shares is not None and col_shares < len(row):
                    sv = _parse_number(row[col_shares])
                    if sv is not None and sv > 0:
                        shares = int(sv)

                purpose = ""
                if col_purpose is not None and col_purpose < len(row):
                    purpose = row[col_purpose][:80]

                if amount_m is None and shares is None:
                    continue
                seen.add(target_name)
                relations.append(
                    ShareholdingRelation(
                        target_name=target_name,
                        shares=shares,
                        amount_million_yen=amount_m,
                        purpose=purpose,
                    )
                )
    return relations


def extract_major_shareholders(soups: list[BeautifulSoup]) -> list[MajorShareholderRelation]:
    """iXBRL内の「大株主の状況」テーブルから法人大株主を抽出する（信託口・持株会は除外）。"""
    relations: list[MajorShareholderRelation] = []
    seen: set[str] = set()

    for soup in soups:
        for table in soup.find_all("table"):
            rows = _table_rows(table)
            if len(rows) < 2:
                continue
            header = rows[0]
            header_joined = " ".join(header)
            context = _preceding_context_text(table)

            is_sh_table = (
                any(k in header_joined for k in ("氏名又は名称", "株主名", "氏名または名称"))
                and any(k in header_joined for k in ("所有株式数", "持株比率", "総数に対する"))
            ) or ("大株主の状況" in context and "所有株式数" in header_joined)
            if not is_sh_table:
                continue

            col_name = next((i for i, h in enumerate(header) if any(k in h for k in ("氏名", "名称", "株主"))), 0)
            col_ratio = next((i for i, h in enumerate(header) if any(k in h for k in ("割合", "比率", "%"))), None)
            col_shares = next((i for i, h in enumerate(header) if "株式数" in h), None)

            for row in rows[1:]:
                if col_name >= len(row):
                    continue
                sh_name = row[col_name]
                if not sh_name or is_excluded_counterparty(sh_name) or sh_name in seen:
                    continue
                ratio = _parse_number(row[col_ratio]) if col_ratio is not None and col_ratio < len(row) else None
                if ratio is not None and (ratio <= 0 or ratio > 100):
                    ratio = None
                shares_val = _parse_number(row[col_shares]) if col_shares is not None and col_shares < len(row) else None
                shares = int(shares_val) if shares_val is not None and shares_val > 0 else None
                if ratio is None and shares is None:
                    continue
                seen.add(sh_name)
                relations.append(
                    MajorShareholderRelation(
                        shareholder_name=sh_name,
                        ratio_percent=ratio,
                        shares=shares,
                    )
                )
    return relations


def extract_sections_from_soups(soups: list[BeautifulSoup]) -> dict[str, str]:
    """対象セクション本文を抽出する（iXBRL TextBlock優先＋見出しフォールバック）。"""
    sections: dict[str, str] = {}
    textblock_map = {
        "InformationAboutMajorCustomersTextBlock": "主要な顧客ごとの情報",
        "CriticalContractsForOperationTextBlock": "経営上の重要な契約等",
        "BusinessRisksTextBlock": "事業等のリスク",
        "ManagementAnalysisOfFinancialPositionOperatingResultsAndCashFlowsTextBlock": "経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析",
        "ResearchAndDevelopmentActivitiesTextBlock": "研究開発活動",
    }

    for soup in soups:
        for tag in soup.find_all(attrs={"name": True}):
            local = str(tag.get("name") or "").split(":")[-1]
            if local in textblock_map and textblock_map[local] not in sections:
                txt = _norm(tag.get_text(" ", strip=True))
                if len(txt) >= 30:
                    sections[textblock_map[local]] = txt[:6000]

    if not sections:
        full_text = "\n".join(_norm(s.get_text("\n", strip=True)) for s in soups)
        for heading in TARGET_SECTIONS:
            idx = full_text.find(heading)
            if idx >= 0:
                ends = [full_text.find(h, idx + len(heading)) for h in TARGET_SECTIONS]
                ends = [e for e in ends if e > idx]
                end = min(ends) if ends else min(len(full_text), idx + 4000)
                sections[heading] = full_text[idx:end].strip()[:6000]

    return sections


def extract_text_cooccurrences(
    sections: dict[str, str],
    resolver: CompanyResolver | None,
    *,
    filer_code: str = "",
) -> list[TextRelation]:
    """ローカルPythonのみで本文テキスト中の上場企業名×関係キーワード共起を抽出する。"""
    if resolver is None or not sections:
        return []

    best_by_code: dict[str, TextRelation] = {}
    narrative_keys = (
        "経営上の重要な契約等",
        "事業等のリスク",
        "経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析",
        "研究開発活動",
    )

    for sec_key in narrative_keys:
        body = sections.get(sec_key)
        if not body:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(body):
            s = sentence.strip()
            if len(s) < 12 or len(s) > 400:
                continue
            matched_rule: tuple[str, int] | None = None
            for kind, keywords, score in TEXT_RELATION_RULES:
                if any(kw in s for kw in keywords):
                    matched_rule = (kind, score)
                    break
            if not matched_rule:
                continue
            kind, score = matched_rule
            mentions = resolver.find_mentioned_codes(s, exclude_code=filer_code, limit=4)
            for code, matched_name in mentions:
                prev = best_by_code.get(code)
                if prev is None or score > prev.score:
                    best_by_code[code] = TextRelation(
                        counterparty_code=code,
                        counterparty_name=matched_name,
                        kind=kind,
                        score=score,
                        snippet=s[:120],
                    )

    return list(best_by_code.values())


def build_excerpt(sections: dict[str, str], customers: list[CustomerRelation]) -> str:
    """詳細パネルに表示する200文字前後の有報要約抜粋を作成する。"""
    if customers:
        top = ", ".join(c.counterparty for c in customers[:3])
        return f"【主要な顧客】{top} 等との取引を開示。" + (
            sections.get("事業等のリスク", "")[:140]
        )
    for key in (
        "経営上の重要な契約等",
        "事業等のリスク",
        "経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析",
    ):
        txt = sections.get(key, "")
        if txt:
            return f"【{key}】{txt[:180]}"
    return ""


def _load_public_soups(zip_path: Path) -> list[BeautifulSoup]:
    soups: list[BeautifulSoup] = []
    with zipfile.ZipFile(zip_path) as zf:
        htm_candidates = [
            name
            for name in zf.namelist()
            if "PublicDoc" in name and name.lower().endswith((".htm", ".html"))
        ]
        if not htm_candidates:
            htm_candidates = [name for name in zf.namelist() if name.lower().endswith((".htm", ".html", ".xbrl"))]

        for name in sorted(htm_candidates):
            raw = zf.read(name)
            parser = "lxml" if name.lower().endswith((".htm", ".html")) else "xml"
            try:
                soups.append(BeautifulSoup(raw, parser))
            except Exception:  # noqa: BLE001
                soups.append(BeautifulSoup(raw, "html.parser"))
    return soups


def parse_zip(
    zip_path: Path,
    *,
    resolver: CompanyResolver | None = None,
    manifest_entry: dict | None = None,
) -> ExtractedFiling:
    """EDINETのXBRL ZIPから定型表・本文共起・抜粋を一括抽出する。"""
    doc_id = zip_path.stem
    soups = _load_public_soups(zip_path)
    meta = _extract_filer_metadata(soups)

    filer_name = meta.get("filer_name") or str((manifest_entry or {}).get("filerName") or "")
    edinet_code = meta.get("edinet_code") or str((manifest_entry or {}).get("edinetCode") or "")
    sec_code = meta.get("securities_code") or clean_sec_code((manifest_entry or {}).get("secCode"))
    if not sec_code and edinet_code and resolver:
        sec_code = resolver.resolve_edinet_code(edinet_code) or ""
    fiscal_year = meta.get("fiscal_year") or str((manifest_entry or {}).get("periodEnd") or "")[:7]

    sections = extract_sections_from_soups(soups)
    customers = extract_customers_from_tables(soups)
    cross_shareholdings = extract_cross_shareholdings(soups)
    major_shareholders = extract_major_shareholders(soups)
    text_relations = extract_text_cooccurrences(sections, resolver, filer_code=sec_code)
    excerpt = build_excerpt(sections, customers)

    # キャッシュ軽量化のため sections 全文は抜粋のみ保持（生テキスト肥大化を防止）
    trimmed_sections = {k: v[:1200] for k, v in sections.items()}

    return ExtractedFiling(
        doc_id=doc_id,
        filer_name=filer_name,
        edinet_code=edinet_code,
        securities_code=sec_code,
        fiscal_year=fiscal_year,
        excerpt=excerpt,
        sections=trimmed_sections,
        customers=customers,
        cross_shareholdings=cross_shareholdings,
        major_shareholders=major_shareholders,
        text_relations=text_relations,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="有価証券報告書 iXBRL パーサ（定型表＋本文共起）")
    parser.add_argument("--zip", type=Path, required=True, help="EDINETから取得したXBRL ZIPのパス")
    parser.add_argument("--master", type=Path, default=None, help="企業マスタJSON（本文共起抽出用・任意）")
    parser.add_argument("--out", type=Path, default=None, help="抽出結果JSONの保存先（省略時は標準出力）")
    args = parser.parse_args(argv)

    resolver = None
    if args.master and args.master.exists():
        resolver = CompanyResolver(load_company_master(args.master))

    filing = parse_zip(args.zip, resolver=resolver)
    text = json.dumps(asdict(filing), ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(
            f"抽出完了 ({filing.securities_code or filing.doc_id}): "
            f"顧客={len(filing.customers)} / 政策保有={len(filing.cross_shareholdings)} / "
            f"大株主={len(filing.major_shareholders)} / 本文共起={len(filing.text_relations)} -> {args.out}",
            file=sys.stderr,
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
