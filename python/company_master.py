"""JPX上場銘柄一覧・EDINETコードリストの同期および企業名自動名寄せ（Entity Resolution）。

全上場企業（プライム・スタンダード・グロース約3,800社）のマスタを無料ソースから構築し、
有報内の相手先名称（表記揺れ・法人格・注記付き）を4桁証券コードへ高精度に名寄せする。

使い方:
    python python/company_master.py sync --out data/cache/company_master.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import unicodedata
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import requests

JPX_LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
EDINET_CODELIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"

# 信託口・名義カストディアン・持株会など「事業・資本関係ではない形式的大株主」の除外パターン
EXCLUDED_COUNTERPARTY_PATTERNS = (
    "日本マスタートラスト信託銀行",
    "日本カストディ銀行",
    "資産管理サービス信託銀行",
    "日本トラスティ・サービス信託銀行",
    "マスタートラスト",
    "カストディ",
    "ステートストリート",
    "statestreet",
    "state street",
    "jpmorgan",
    "jpモルガン",
    "bbh",
    "brown brothers",
    "bnym",
    "bank of new york",
    "citibank",
    "hsbc",
    "goldman sachs",
    "ubs ag",
    "credit suisse",
    "morgan stanley",
    "northern trust",
    "従業員持株会",
    "社員持株会",
    "取引先持株会",
    "役員持株会",
    "持株会",
    "自己株式",
    "自社株",
    "その他",
    "合計",
    "小計",
    "計",
)

# JPX 33業種 → 視覚的に分かりやすい集約セクター（色分けの視認性向上用）
SECTOR33_TO_GROUP: dict[str, str] = {
    "輸送用機器": "自動車・モビリティ",
    "ゴム製品": "自動車・モビリティ",
    "陸運業": "自動車・モビリティ",
    "海運業": "自動車・モビリティ",
    "空運業": "自動車・モビリティ",
    "電気機器": "半導体・電機",
    "精密機器": "半導体・電機",
    "機械": "半導体・電機",
    "情報・通信業": "通信・IT",
    "サービス業": "通信・IT",
    "卸売業": "商社・エネルギー",
    "石油・石炭製品": "商社・エネルギー",
    "鉱業": "商社・エネルギー",
    "電気・ガス業": "商社・エネルギー",
    "銀行業": "金融",
    "証券、商品先物取引業": "金融",
    "保険業": "金融",
    "その他金融業": "金融",
    "化学": "素材・重工",
    "鉄鋼": "素材・重工",
    "非鉄金属": "素材・重工",
    "金属製品": "素材・重工",
    "ガラス・土石製品": "素材・重工",
    "パルプ・紙": "素材・重工",
    "繊維製品": "素材・重工",
    "建設業": "不動産・建設",
    "不動産業": "不動産・建設",
    "倉庫・運輸関連業": "不動産・建設",
    "食料品": "消費・医薬",
    "小売業": "消費・医薬",
    "医薬品": "消費・医薬",
    "水産・農林業": "消費・医薬",
    "その他製品": "消費・医薬",
}

_PAREN_NOTE_RE = re.compile(r"[\(（\[【][^\)）\]】]{0,40}[\)）\]】]")
_CORP_DECORATION_RE = re.compile(
    r"(株式会社|合同会社|有限会社|合資会社|合名会社|一般社団法人|公益社団法人|独立行政法人|"
    r"㈱|\(株\)|（株）|\(有\)|（有）|co\.?,?\s*ltd\.?|corp\.?(oration)?|inc\.?|ltd\.?|holdings?|"
    r"ホールディングス|ホールディング|hd$)",
    re.IGNORECASE,
)
_SPACE_PUNCT_RE = re.compile(r"[\s\u3000・･\-‐‑‒–—―,.、。/／&＆*※†‡]+")


def normalize_company_name(name: str, *, strip_holding_suffix: bool = False) -> str:
    """企業名の表記揺れを吸収した正規化キーを返す。"""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name)).strip()
    # 末尾の注記番号（例: "トヨタ自動車 ※1", "デンソー *2"）を除去
    s = re.sub(r"[※*†‡]\s*\d*$", "", s).strip()
    # 括弧内の注記（例: "（注1）", "（信託口）"）を除去。ただし (株) は法人格側で落とす
    s = _PAREN_NOTE_RE.sub("", s)
    if strip_holding_suffix:
        s = _CORP_DECORATION_RE.sub("", s)
    else:
        # ホールディングスは残した版（完全一致優先用）
        s = re.sub(
            r"(株式会社|合同会社|有限会社|合資会社|合名会社|㈱|co\.?,?\s*ltd\.?|corp\.?(oration)?|inc\.?|ltd\.?)",
            "",
            s,
            flags=re.IGNORECASE,
        )
    s = _SPACE_PUNCT_RE.sub("", s)
    return s.lower()


def is_excluded_counterparty(name: str) -> bool:
    """信託口・持株会・合計行など企業間関係として除外すべき名称かを判定する。"""
    if not name:
        return True
    norm = unicodedata.normalize("NFKC", name).strip().lower()
    if len(norm) < 2:
        return True
    if "信託口" in norm or "常任代理人" in norm or "omnibus" in norm or "custody" in norm:
        return True
    for pat in EXCLUDED_COUNTERPARTY_PATTERNS:
        if pat.lower() in norm:
            return True
    return False


def map_sector_group(sector33: str) -> str:
    """JPX 33業種を表示用の集約セクター名へ変換する。"""
    cleaned = unicodedata.normalize("NFKC", sector33 or "").strip()
    if not cleaned or cleaned == "-":
        return "その他"
    return SECTOR33_TO_GROUP.get(cleaned, cleaned)


def clean_sec_code(raw: str | int | float | None) -> str:
    """証券コードを4文字コードへ正規化（5桁EDINET形式は末尾0を落とす。英字コード130A等も保持）。"""
    if raw is None:
        return ""
    if isinstance(raw, float):
        if raw.is_integer():
            s = str(int(raw))
        else:
            s = str(raw)
    else:
        s = str(raw)
    s = unicodedata.normalize("NFKC", s).strip().upper()
    if s.endswith(".0"):
        s = s[:-2]
    if len(s) == 5 and s.endswith("0"):
        s = s[:4]
    if re.fullmatch(r"[0-9A-Z]{4}", s):
        return s
    return ""


@dataclass
class CompanyRecord:
    code: str
    name: str
    sector: str = "その他"
    sector33: str = ""
    market: str = ""
    edinet_code: str = ""
    capital_trillion: float = 0.0
    market_cap_trillion: float = 0.0
    domain: str = ""
    aliases: list[str] = field(default_factory=list)


def _normalize_market(raw: str) -> str:
    s = unicodedata.normalize("NFKC", str(raw or "")).strip()
    if "プライム" in s:
        return "プライム"
    if "スタンダード" in s:
        return "スタンダード"
    if "グロース" in s:
        return "グロース"
    return ""


def fetch_jpx_master(timeout: int = 60) -> dict[str, CompanyRecord]:
    """JPX公式の上場銘柄一覧（data_j.xls）を取得して CompanyRecord 辞書を返す。"""
    try:
        import xlrd  # type: ignore
    except ImportError as exc:
        raise RuntimeError("JPX一覧の読み込みには xlrd が必要です: pip install xlrd") from exc

    resp = requests.get(JPX_LIST_URL, timeout=timeout, headers={"User-Agent": "Git_stock_networkmap/1.0"})
    resp.raise_for_status()

    wb = xlrd.open_workbook(file_contents=resp.content)
    sheet = wb.sheet_by_index(0)
    if sheet.nrows < 2:
        return {}

    headers = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
    col = {h: idx for idx, h in enumerate(headers)}

    def find_col(*keywords: str) -> int | None:
        for h, idx in col.items():
            if any(k in h for k in keywords):
                return idx
        return None

    idx_code = find_col("コード")
    idx_name = find_col("銘柄名")
    idx_market = find_col("市場・商品区分", "市場")
    idx_sector33 = find_col("33業種区分")
    if idx_code is None or idx_name is None:
        return {}

    records: dict[str, CompanyRecord] = {}
    for r in range(1, sheet.nrows):
        market_raw = str(sheet.cell_value(r, idx_market)) if idx_market is not None else ""
        # ETF/REIT/インフラファンド等を除外し内国株式のみ対象にする
        if "内国株式" not in market_raw:
            continue
        market = _normalize_market(market_raw)
        if not market:
            continue

        code = clean_sec_code(sheet.cell_value(r, idx_code))
        name = unicodedata.normalize("NFKC", str(sheet.cell_value(r, idx_name))).strip()
        sector33 = str(sheet.cell_value(r, idx_sector33)).strip() if idx_sector33 is not None else ""
        if not code or not name:
            continue

        records[code] = CompanyRecord(
            code=code,
            name=name,
            sector=map_sector_group(sector33),
            sector33=sector33,
            market=market,
        )
    return records


def fetch_edinet_code_list(timeout: int = 60) -> dict[str, dict]:
    """EDINET公式コードリスト（Edinetcode.zip）を取得し、証券コード→メタ辞書を返す。"""
    resp = requests.get(EDINET_CODELIST_URL, timeout=timeout, headers={"User-Agent": "Git_stock_networkmap/1.0"})
    resp.raise_for_status()

    result: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            return result
        raw_bytes = zf.read(csv_names[0])

    # EDINETコードリストは CP932（Shift_JIS）、1行目がメタヘッダ、2行目がカラム見出し
    text = raw_bytes.decode("cp932", errors="replace")
    lines = text.splitlines()
    header_idx = 0
    for i, line in enumerate(lines[:5]):
        if "ＥＤＩＮＥＴコード" in line or "EDINETコード" in line:
            header_idx = i
            break

    reader = csv.DictReader(lines[header_idx:])
    for row in reader:
        sec_raw = row.get("証券コード") or row.get("証券ｺｰﾄﾞ") or ""
        code = clean_sec_code(sec_raw)
        if not code:
            continue
        edinet_code = (row.get("ＥＤＩＮＥＴコード") or row.get("EDINETコード") or "").strip()
        filer_name = unicodedata.normalize("NFKC", (row.get("提出者名") or "").strip())
        sector33 = unicodedata.normalize("NFKC", (row.get("提出者業種") or "").strip())
        cap_raw = (row.get("資本金") or "").replace(",", "").strip()
        capital_trillion = 0.0
        if cap_raw.isdigit():
            # EDINETコードリストの資本金単位は百万円 → 兆円へ換算（1兆円 = 1,000,000百万円）
            capital_trillion = round(int(cap_raw) / 1_000_000, 4)

        result[code] = {
            "edinet_code": edinet_code,
            "filer_name": filer_name,
            "sector33": sector33,
            "capital_trillion": capital_trillion,
        }
    return result


def sync_company_master(
    out_path: Path,
    *,
    alias_overrides_path: Path | None = None,
) -> dict[str, CompanyRecord]:
    """JPX一覧とEDINETコードリストを突合して全上場企業マスタを生成・保存する。"""
    records = fetch_jpx_master()
    edinet_map = fetch_edinet_code_list()

    for code, meta in edinet_map.items():
        if code in records:
            rec = records[code]
            rec.edinet_code = meta["edinet_code"]
            if meta["capital_trillion"] > 0:
                rec.capital_trillion = meta["capital_trillion"]
            if meta["filer_name"] and meta["filer_name"] != rec.name:
                if meta["filer_name"] not in rec.aliases:
                    rec.aliases.append(meta["filer_name"])
        else:
            # JPX取得漏れや直近上場等でEDINET側にのみある上場企業も補完可能だが、
            # 廃止銘柄の混入を防ぐため JPX 側を正本とする
            continue

    if alias_overrides_path and alias_overrides_path.exists():
        overrides = json.loads(alias_overrides_path.read_text(encoding="utf-8"))
        if isinstance(overrides, dict):
            for alias_name, target_code in overrides.items():
                c = clean_sec_code(target_code)
                if c in records and alias_name not in records[c].aliases:
                    records[c].aliases.append(alias_name)

    save_company_master(records, out_path)
    return records


def save_company_master(records: dict[str, CompanyRecord], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {code: asdict(rec) for code, rec in sorted(records.items())}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_company_master(path: Path) -> dict[str, CompanyRecord]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    records: dict[str, CompanyRecord] = {}
    for code, item in raw.items():
        records[code] = CompanyRecord(**item)
    return records


class CompanyResolver:
    """企業名文字列 → 上場証券コード（4桁）の高速名寄せリゾルバ。"""

    def __init__(
        self,
        records: dict[str, CompanyRecord],
        alias_overrides: dict[str, str] | None = None,
    ) -> None:
        self.records = records
        self._exact_index: dict[str, str] = {}
        self._loose_index: dict[str, str] = {}
        self._edinet_to_code: dict[str, str] = {}
        self.unmatched_counter: Counter[str] = Counter()

        # 衝突チェック用（同名・略称重複の誤爆を防ぐ）
        loose_counts: Counter[str] = Counter()
        loose_candidates: list[tuple[str, str]] = []

        for code, rec in records.items():
            if rec.edinet_code:
                self._edinet_to_code[rec.edinet_code] = code
            names = [rec.name, *rec.aliases]
            for nm in names:
                exact = normalize_company_name(nm, strip_holding_suffix=False)
                if len(exact) >= 2:
                    self._exact_index.setdefault(exact, code)
                loose = normalize_company_name(nm, strip_holding_suffix=True)
                if len(loose) >= 2:
                    loose_counts[loose] += 1
                    loose_candidates.append((loose, code))

        for loose, code in loose_candidates:
            if loose_counts[loose] == 1:
                self._loose_index[loose] = code

        if alias_overrides:
            for raw_alias, target_code in alias_overrides.items():
                c = clean_sec_code(target_code)
                if not c:
                    continue
                exact = normalize_company_name(raw_alias, strip_holding_suffix=False)
                loose = normalize_company_name(raw_alias, strip_holding_suffix=True)
                if exact:
                    self._exact_index[exact] = c
                if loose:
                    self._loose_index[loose] = c

        # 本文共起スキャン用の検索語一覧（長さ降順・3文字以上で誤爆を防止）
        self._scan_terms: list[tuple[str, str]] = []
        seen_terms: set[str] = set()
        for code, rec in records.items():
            for nm in [rec.name, *rec.aliases]:
                cleaned = unicodedata.normalize("NFKC", nm).strip()
                cleaned = re.sub(r"^(株式会社|㈱)|((株式会社|㈱))$", "", cleaned).strip()
                if len(cleaned) >= 3 and cleaned not in seen_terms and not is_excluded_counterparty(cleaned):
                    seen_terms.add(cleaned)
                    self._scan_terms.append((cleaned, code))
        self._scan_terms.sort(key=lambda pair: len(pair[0]), reverse=True)

    def resolve_edinet_code(self, edinet_code: str) -> str | None:
        return self._edinet_to_code.get((edinet_code or "").strip())

    def resolve(self, raw_name: str, *, exclude_code: str = "") -> str | None:
        """相手企業名を証券コードへ解決する。見つからない場合は None を返し未一致ログへ記録。"""
        if is_excluded_counterparty(raw_name):
            return None

        # 直接4桁コードが書かれているケースを許容
        direct_code = clean_sec_code(raw_name)
        if direct_code and direct_code in self.records and direct_code != exclude_code:
            return direct_code

        exact = normalize_company_name(raw_name, strip_holding_suffix=False)
        code = self._exact_index.get(exact)
        if not code:
            loose = normalize_company_name(raw_name, strip_holding_suffix=True)
            code = self._loose_index.get(loose)

        if code and code == exclude_code:
            return None
        if code:
            return code

        cleaned_label = unicodedata.normalize("NFKC", raw_name).strip()
        if len(cleaned_label) >= 2:
            self.unmatched_counter[cleaned_label] += 1
        return None

    def find_mentioned_codes(self, text: str, *, exclude_code: str = "", limit: int = 8) -> list[tuple[str, str]]:
        """本文テキスト中に言及されている上場企業 (code, matched_name) を抽出する。"""
        if not text:
            return []
        norm_text = unicodedata.normalize("NFKC", text)
        found: list[tuple[str, str]] = []
        seen_codes: set[str] = {exclude_code} if exclude_code else set()

        for term, code in self._scan_terms:
            if code in seen_codes:
                continue
            if term in norm_text:
                seen_codes.add(code)
                found.append((code, term))
                if len(found) >= limit:
                    break
        return found

    def top_unmatched(self, n: int = 100) -> list[dict]:
        return [{"name": name, "count": count} for name, count in self.unmatched_counter.most_common(n)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JPX・EDINET 企業マスタ同期ツール")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="JPX一覧＋EDINETコードリストから企業マスタを同期する")
    p_sync.add_argument("--out", type=Path, default=Path("data/cache/company_master.json"))
    p_sync.add_argument("--aliases", type=Path, default=Path("data/alias_overrides.json"))

    args = parser.parse_args(argv)
    if args.command == "sync":
        records = sync_company_master(args.out, alias_overrides_path=args.aliases)
        print(f"企業マスタを同期しました: {len(records)} 社 -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
