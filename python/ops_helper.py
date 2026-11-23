"""Phase 4 運用管理・AI Pro オンデマンド深掘り補助ツール。

外部LLM APIキーを使わず、既存の AI Pro（Antigravity / Gemini）契約および
Google Sheets 運用管理シートと Git リポジトリをシームレスに連携するための軽量CLI。

使い方:
    # 1. AI Pro 解析用に注目企業の有報本文ダイジェストを書き出す
    python python/ops_helper.py export-for-ai --extracted-dir data/cache/extracted --codes 7203,6758,8035 --out /tmp/ai_digest.md

    # 2. AI Pro が抽出した関係JSONをバリデーションして data/llm_relations.json へマージ
    python python/ops_helper.py merge-llm --input /tmp/new_relations.json --target data/llm_relations.json

    # 3. Google Sheets（alias_overrides タブ）と data/alias_overrides.json をCSV経由で同期
    python python/ops_helper.py aliases-to-csv --aliases data/alias_overrides.json --out /tmp/aliases.csv
    python python/ops_helper.py csv-to-aliases --csv /tmp/aliases.csv --aliases data/alias_overrides.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

try:
    from company_master import clean_sec_code
except ImportError:
    from python.company_master import clean_sec_code  # type: ignore

VALID_KINDS = {
    "主要顧客・供給",
    "政策保有株式",
    "大株主",
    "資本・業務提携",
    "共同開発・協業",
    "供給網・リスク",
}


def merge_llm_relations(incoming: list[dict], target_path: Path) -> tuple[int, int]:
    """AI Pro 抽出関係JSONを検証・重複排除して data/llm_relations.json へ追記統合する。"""
    existing: list[dict] = []
    if target_path.exists():
        raw = json.loads(target_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            existing = raw

    by_key: dict[tuple[str, str, str], dict] = {}
    for item in existing:
        s = clean_sec_code(item.get("source"))
        t = clean_sec_code(item.get("target"))
        kind = str(item.get("kind") or "資本・業務提携")
        if s and t and s != t:
            lo, hi = (s, t) if s <= t else (t, s)
            by_key[(lo, hi, kind)] = item

    added = 0
    for item in incoming:
        s = clean_sec_code(item.get("source"))
        t = clean_sec_code(item.get("target"))
        if not s or not t or s == t:
            continue
        kind = str(item.get("kind") or "資本・業務提携").strip()
        if kind not in VALID_KINDS:
            kind = "資本・業務提携"
        val = int(max(10, min(85, int(item.get("value") or 60))))
        record = {
            "source": s,
            "target": t,
            "value": val,
            "kind": kind,
            "detail": str(item.get("detail") or "").strip()[:120],
            "docId": str(item.get("docId") or "").strip(),
        }
        lo, hi = (s, t) if s <= t else (t, s)
        key = (lo, hi, kind)
        if key not in by_key:
            added += 1
        by_key[key] = record

    merged = sorted(by_key.values(), key=lambda r: (r["source"], r["target"], r["kind"]))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return added, len(merged)


def export_for_ai(extracted_dir: Path, codes: set[str], out_path: Path, limit: int = 20) -> int:
    """抽出済み有報JSONから AI Pro 解析用の軽量Markdownダイジェストを書き出す。"""
    chunks: list[str] = [
        "# AI Pro 深掘り解析用 有報本文ダイジェスト\n",
        "各企業について、提携・合弁・共同開発・主要供給網の相手上場企業を以下のJSON配列形式で抽出してください：\n",
        '`[{"source": "7203", "target": "6758", "kind": "資本・業務提携", "value": 70, "detail": "根拠1行", "docId": "S100XXXX"}]`\n',
    ]
    count = 0
    for p in sorted(extracted_dir.glob("*.json")):
        if count >= limit:
            break
        item = json.loads(p.read_text(encoding="utf-8"))
        code = clean_sec_code(item.get("securities_code"))
        if codes and code not in codes:
            continue
        sections = item.get("sections") or {}
        if not sections:
            continue
        chunks.append(f"\n## {code} {item.get('filer_name', '')} (docId: {item.get('doc_id', '')})")
        for sec_name, body in sections.items():
            if body:
                chunks.append(f"### {sec_name}\n{str(body)[:800]}")
        count += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(chunks) + "\n", encoding="utf-8")
    return count


def aliases_to_csv(aliases_path: Path, out_csv: Path) -> int:
    data = json.loads(aliases_path.read_text(encoding="utf-8"))
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["原文表記", "証券コード"])
        for name, code in sorted(data.items()):
            writer.writerow([name, code])
    return len(data)


def csv_to_aliases(in_csv: Path, aliases_path: Path) -> int:
    existing = json.loads(aliases_path.read_text(encoding="utf-8")) if aliases_path.exists() else {}
    with in_csv.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        for row in reader:
            if len(row) < 2:
                continue
            name = row[0].strip()
            code = clean_sec_code(row[1])
            if name and code:
                existing[name] = code
    aliases_path.parent.mkdir(parents=True, exist_ok=True)
    aliases_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(existing)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 4 運用管理・AI Pro 深掘り補助ツール")
    sub = parser.add_subparsers(dest="command", required=True)

    p_exp = sub.add_parser("export-for-ai", help="AI Pro 解析用の本文ダイジェストを生成する")
    p_exp.add_argument("--extracted-dir", type=Path, default=Path("data/cache/extracted"))
    p_exp.add_argument("--codes", default="", help="対象証券コード（カンマ区切り、省略時は先頭N社）")
    p_exp.add_argument("--limit", type=int, default=20)
    p_exp.add_argument("--out", type=Path, required=True)

    p_merge = sub.add_parser("merge-llm", help="AI Pro 抽出関係JSONを data/llm_relations.json へマージする")
    p_merge.add_argument("--input", type=Path, required=True)
    p_merge.add_argument("--target", type=Path, default=Path("data/llm_relations.json"))

    p_a2c = sub.add_parser("aliases-to-csv", help="alias_overrides.json を CSV へ書き出す")
    p_a2c.add_argument("--aliases", type=Path, default=Path("data/alias_overrides.json"))
    p_a2c.add_argument("--out", type=Path, required=True)

    p_c2a = sub.add_parser("csv-to-aliases", help="CSV から alias_overrides.json へ取り込む")
    p_c2a.add_argument("--csv", type=Path, required=True)
    p_c2a.add_argument("--aliases", type=Path, default=Path("data/alias_overrides.json"))

    args = parser.parse_args(argv)
    if args.command == "export-for-ai":
        codes = {clean_sec_code(c) for c in args.codes.split(",") if clean_sec_code(c)}
        n = export_for_ai(args.extracted_dir, codes, args.out, limit=args.limit)
        print(f"AI Pro 用ダイジェストを書き出しました: {n} 社 -> {args.out}", file=sys.stderr)
    elif args.command == "merge-llm":
        incoming = json.loads(args.input.read_text(encoding="utf-8"))
        added, total = merge_llm_relations(incoming, args.target)
        print(f"マージ完了: 新規 {added} 件 / 合計 {total} 件 -> {args.target}", file=sys.stderr)
    elif args.command == "aliases-to-csv":
        n = aliases_to_csv(args.aliases, args.out)
        print(f"CSV出力完了: {n} 件 -> {args.out}", file=sys.stderr)
    elif args.command == "csv-to-aliases":
        n = csv_to_aliases(args.csv, args.aliases)
        print(f"辞書更新完了: 合計 {n} 件 -> {args.aliases}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
