"""抽出結果をフロントエンド用ネットワークJSON（単一／分割静的JSON）へ整形する。

入力:
  - parse_xbrl.py が出力した抽出結果JSON（単体・配列・またはディレクトリ配下の複数JSON）
  - 企業マスタ（company_master.json：JPX＋EDINETコードリスト）
  - 時価総額・ドメインキャッシュ（market_caps.json：任意）
  - AI Pro オンデマンド本文抽出キャッシュ（data/llm_relations.json：任意）
  - 名寄せ例外辞書（data/alias_overrides.json：任意）

出力:
  - 単一モード: --out network.json（従来互換）
  - 分割配信モード: --split-dir public/data
      - public/data/meta.json
      - public/data/index.json
      - public/data/core.json
      - public/data/network.json
      - public/data/details/{0-9,other}.json

使い方:
    python python/build_network.py --sample --out network.json
    python python/build_network.py --extracted-dir data/cache/extracted --master data/cache/company_master.json --split-dir public/data
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    from company_master import (
        CompanyRecord,
        CompanyResolver,
        clean_sec_code,
        has_corporate_mention,
        is_ambiguous_text_term,
        load_company_master,
    )
    from market_data import load_market_cache
except ImportError:
    from python.company_master import (  # type: ignore
        CompanyRecord,
        CompanyResolver,
        clean_sec_code,
        has_corporate_mention,
        is_ambiguous_text_term,
        load_company_master,
    )
    from python.market_data import load_market_cache  # type: ignore


def logo_url(code: str) -> str:
    return f"https://logoapi.sh/ticker/{code}.svg"


def score_customer(ratio_percent: float | None, amount_million_yen: float | None) -> int:
    if ratio_percent is not None and ratio_percent > 0:
        return max(20, min(100, round(ratio_percent * 2.5 + 25)))
    if amount_million_yen is not None and amount_million_yen > 0:
        # 10億円=35, 100億円=60, 1000億円=85 目安
        return max(20, min(95, round(math.log10(max(1.0, amount_million_yen)) * 22 - 30)))
    return 50


def score_cross_shareholding(amount_million_yen: float | None) -> int:
    if amount_million_yen is None or amount_million_yen <= 0:
        return 35
    # 10億円(1,000百万円)→40, 100億円(10,000百万円)→65, 1,000億円(100,000百万円)→90
    val = round(math.log10(max(10.0, amount_million_yen)) * 25 - 35)
    return max(20, min(96, val))


def score_major_shareholder(ratio_percent: float | None) -> int:
    if ratio_percent is None or ratio_percent <= 0:
        return 35
    return max(25, min(100, round(ratio_percent * 2.0 + 20)))


def _make_logo_text(name: str, code: str) -> str:
    ascii_letters = "".join(ch for ch in name if ch.isascii() and ch.isalnum())
    if len(ascii_letters) >= 2:
        return ascii_letters[:2].upper()
    return code[:2]


def _canonical_pair(a: str, b: str, kind: str) -> tuple[str, str, str]:
    # 同一ペア・同種別の重複エッジを統合（順序は数値が大きい方を優先して残す）
    lo, hi = (a, b) if a <= b else (b, a)
    return (lo, hi, kind)


def build_from_extracted(
    filings: list[dict],
    *,
    master: dict[str, CompanyRecord] | None = None,
    market_cache: dict[str, dict] | None = None,
    alias_overrides: dict[str, str] | None = None,
    llm_relations: list[dict] | None = None,
    unmatched_out: Path | None = None,
) -> tuple[dict, dict[str, dict]]:
    """抽出結果配列＋マスタからネットワークデータと詳細マップを構築する。"""
    records: dict[str, CompanyRecord] = dict(master or {})
    market_cache = market_cache or {}

    # マスタが無い単発実行時でも filings から最低限のレコードを補完する
    for f in filings:
        code = clean_sec_code(f.get("securities_code"))
        if code and code not in records:
            records[code] = CompanyRecord(
                code=code,
                name=str(f.get("filer_name") or code),
                edinet_code=str(f.get("edinet_code") or ""),
            )

    resolver = CompanyResolver(records, alias_overrides=alias_overrides)

    nodes_map: dict[str, dict] = {}
    details_map: dict[str, dict] = {}
    edge_best: dict[tuple[str, str, str], dict] = {}

    def ensure_node(code: str, fallback_name: str = "") -> dict | None:
        c = clean_sec_code(code)
        if not c:
            return None
        if c not in nodes_map:
            rec = records.get(c)
            m_info = market_cache.get(c, {})
            name = rec.name if rec else (fallback_name or c)
            mcap = float(m_info.get("marketCap") or (rec.market_cap_trillion if rec else 0.0) or 0.0)
            cap = float((rec.capital_trillion if rec else 0.0) or 0.0)
            domain = str(m_info.get("domain") or (rec.domain if rec else "") or "")
            node_obj: dict = {
                "id": c,
                "code": c,
                "name": name,
                "sector": rec.sector if rec else "その他",
                "marketCap": round(mcap, 4),
                "capital": round(cap, 4),
                "logo": logo_url(c),
                "logoText": _make_logo_text(name, c),
            }
            if rec and rec.sector33:
                node_obj["subSector"] = rec.sector33
            if rec and rec.market:
                node_obj["market"] = rec.market
            if rec and rec.edinet_code:
                node_obj["edinetCode"] = rec.edinet_code
            if domain:
                node_obj["domain"] = domain
            nodes_map[c] = node_obj
        return nodes_map[c]

    def add_edge(
        source_code: str,
        target_code: str,
        *,
        value: int,
        kind: str,
        source_type: str,
        detail: str = "",
        amount_million_yen: float | None = None,
        ratio_percent: float | None = None,
        doc_id: str = "",
    ) -> None:
        if not source_code or not target_code or source_code == target_code:
            return
        ensure_node(source_code)
        ensure_node(target_code)
        key = _canonical_pair(source_code, target_code, kind)
        candidate: dict = {
            "source": source_code,
            "target": target_code,
            "value": int(max(5, min(100, value))),
            "kind": kind,
            "sourceType": source_type,
        }
        if detail:
            candidate["detail"] = detail[:120]
        if amount_million_yen is not None:
            candidate["amountMillionYen"] = round(float(amount_million_yen), 1)
        if ratio_percent is not None:
            candidate["ratioPercent"] = round(float(ratio_percent), 2)
        if doc_id:
            candidate["docId"] = doc_id

        prev = edge_best.get(key)
        if prev is None:
            edge_best[key] = candidate
        else:
            # 定型表（table）は本文抽出（llm）より優先、同ソース種別ならスコアが大きい方を採用
            prev_rank = (1 if prev.get("sourceType") == "table" else 0, prev["value"])
            cur_rank = (1 if source_type == "table" else 0, candidate["value"])
            if cur_rank > prev_rank:
                edge_best[key] = candidate

    for filing in filings:
        filer_code = clean_sec_code(filing.get("securities_code"))
        if not filer_code and filing.get("edinet_code"):
            filer_code = resolver.resolve_edinet_code(str(filing.get("edinet_code"))) or ""
        if not filer_code:
            continue

        doc_id = str(filing.get("doc_id") or "")
        fiscal_year = str(filing.get("fiscal_year") or "")
        node = ensure_node(filer_code, str(filing.get("filer_name") or ""))
        if node and fiscal_year:
            node["fiscalYear"] = fiscal_year

        excerpt = str(filing.get("excerpt") or "").strip()
        if excerpt or doc_id:
            details_map[filer_code] = {
                "excerpt": excerpt,
                "docId": doc_id,
                "fiscalYear": fiscal_year,
            }

        # 1) 主要な顧客ごとの情報（定型表）
        for rel in filing.get("customers", []):
            c_code = clean_sec_code(rel.get("counterparty_code")) or resolver.resolve(
                str(rel.get("counterparty") or ""),
                exclude_code=filer_code,
            )
            if not c_code:
                continue
            ratio = rel.get("ratio_percent")
            amount_m = rel.get("amount_million_yen")
            add_edge(
                filer_code,
                c_code,
                value=score_customer(ratio, amount_m),
                kind="主要顧客・供給",
                source_type="table",
                detail=str(rel.get("note") or ""),
                amount_million_yen=amount_m,
                ratio_percent=ratio,
                doc_id=doc_id,
            )

        # 2) 特定投資株式＝政策保有株式（定型表）
        for rel in filing.get("cross_shareholdings", []):
            t_code = clean_sec_code(rel.get("target_code")) or resolver.resolve(
                str(rel.get("target_name") or ""),
                exclude_code=filer_code,
            )
            if not t_code:
                continue
            amount_m = rel.get("amount_million_yen")
            # 旧パーサキャッシュ由来の異常値（単一保有5兆円超＝株数・千円混入等）は除外して安全表示
            if amount_m is not None and amount_m > 5_000_000:
                amount_m = None
            purpose = str(rel.get("purpose") or "").strip()
            detail_parts = []
            if amount_m:
                detail_parts.append(f"保有額 {amount_m:,.0f}百万円")
            if purpose:
                detail_parts.append(purpose[:60])
            add_edge(
                filer_code,
                t_code,
                value=score_cross_shareholding(amount_m),
                kind="政策保有株式",
                source_type="table",
                detail=" / ".join(detail_parts),
                amount_million_yen=amount_m,
                doc_id=doc_id,
            )

        # 3) 大株主の状況（定型表）
        for rel in filing.get("major_shareholders", []):
            sh_code = clean_sec_code(rel.get("shareholder_code")) or resolver.resolve(
                str(rel.get("shareholder_name") or ""),
                exclude_code=filer_code,
            )
            if not sh_code:
                continue
            ratio = rel.get("ratio_percent")
            detail = f"持株比率 {ratio:.1f}%" if ratio is not None else "主要法人株主"
            add_edge(
                sh_code,
                filer_code,
                value=score_major_shareholder(ratio),
                kind="大株主",
                source_type="table",
                detail=detail,
                ratio_percent=ratio,
                doc_id=doc_id,
            )

        # 4) 本文テキスト共起関係（ローカルNLP）
        for rel in filing.get("text_relations", []):
            raw_name = str(rel.get("counterparty_name") or "")
            t_code = clean_sec_code(rel.get("counterparty_code")) or resolver.resolve(
                raw_name,
                exclude_code=filer_code,
            )
            if not t_code:
                continue
            snippet = str(rel.get("snippet") or "")
            target_rec = master.get(t_code)
            target_name = target_rec.name if target_rec else raw_name
            if (is_ambiguous_text_term(raw_name) or is_ambiguous_text_term(target_name)) and not (
                has_corporate_mention(raw_name, snippet) or has_corporate_mention(target_name, snippet)
            ):
                continue
            add_edge(
                filer_code,
                t_code,
                value=int(rel.get("score") or 50),
                kind=str(rel.get("kind") or "資本・業務提携"),
                source_type="llm",
                detail=snippet[:90],
                doc_id=doc_id,
            )

    # 5) 既存の AI Pro オンデマンドLLM抽出結果（data/llm_relations.json）があれば統合
    for item in llm_relations or []:
        s_code = clean_sec_code(item.get("source")) or resolver.resolve(str(item.get("source_name") or ""))
        t_code = clean_sec_code(item.get("target")) or resolver.resolve(str(item.get("target_name") or ""))
        if not s_code or not t_code:
            continue
        add_edge(
            s_code,
            t_code,
            value=int(item.get("value") or 65),
            kind=str(item.get("kind") or "資本・業務提携"),
            source_type="llm",
            detail=str(item.get("detail") or ""),
            doc_id=str(item.get("docId") or ""),
        )

    if unmatched_out is not None:
        unmatched_out.parent.mkdir(parents=True, exist_ok=True)
        unmatched_out.write_text(
            json.dumps(resolver.top_unmatched(200), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    links = sorted(edge_best.values(), key=lambda e: e["value"], reverse=True)
    degree: Counter[str] = Counter()
    for l in links:
        degree[l["source"]] += 1
        degree[l["target"]] += 1

    # 関係があるノードを優先しつつ、検索インデックスには全マスタ企業を含められるよう degree を記録
    for code, node in nodes_map.items():
        node["degree"] = degree.get(code, 0)

    nodes = sorted(
        nodes_map.values(),
        key=lambda n: (n.get("degree", 0), n.get("marketCap", 0.0)),
        reverse=True,
    )
    return {"nodes": nodes, "links": links}, details_map


def select_core_nodes(nodes: list[dict], *, core_max_nodes: int = 180) -> list[dict]:
    """初期表示用コアノード（主要ハブ）を選定する。

    単純な接続数（政策保有銘柄数）順だと地銀・老舗中小型に偏り、
    ソニー・ファーストリテイリング・東京エレクトロン等の超大型主力株が漏れるため、
    1) 接続あり銘柄の時価総額上位（枠の2/3＝120社）
    2) 残り枠（1/3＝60社）を「接続数 × 企業規模補正」上位の構造ハブ
    のハイブリッドで選定する。
    """
    if len(nodes) <= core_max_nodes:
        return list(nodes)

    connected = [n for n in nodes if (n.get("degree") or 0) >= 1]
    pool = connected if len(connected) >= core_max_nodes else list(nodes)

    mcap_quota = max(1, round(core_max_nodes * 2 / 3))
    by_mcap = sorted(
        pool,
        key=lambda n: (float(n.get("marketCap") or 0.0), int(n.get("degree") or 0)),
        reverse=True,
    )
    picked: list[dict] = []
    picked_ids: set[str] = set()
    for n in by_mcap[:mcap_quota]:
        picked.append(n)
        picked_ids.add(str(n["id"]))

    remaining = [n for n in pool if str(n["id"]) not in picked_ids]
    by_hub = sorted(
        remaining,
        key=lambda n: int(n.get("degree") or 0)
        * (1.0 + 0.65 * math.log10(max(float(n.get("marketCap") or 0.0), 0.05) * 20.0 + 1.0)),
        reverse=True,
    )
    need = core_max_nodes - len(picked)
    for n in by_hub[:need]:
        picked.append(n)
        picked_ids.add(str(n["id"]))
    return picked


def write_split_bundle(
    network: dict,
    details_map: dict[str, dict],
    split_dir: Path,
    *,
    master: dict[str, CompanyRecord] | None = None,
    market_cache: dict[str, dict] | None = None,
    core_max_nodes: int = 180,
) -> dict:
    """フロントエンド高速読込用の分割静的JSON一式（public/data/）を書き出す。"""
    split_dir.mkdir(parents=True, exist_ok=True)
    details_dir = split_dir / "details"
    details_dir.mkdir(parents=True, exist_ok=True)

    nodes: list[dict] = network["nodes"]
    links: list[dict] = network["links"]
    node_by_id = {n["id"]: n for n in nodes}

    # 1) 全上場検索インデックス（index.json）
    index_entries: list[dict] = []
    seen_codes: set[str] = set()
    for n in nodes:
        seen_codes.add(n["code"])
        index_entries.append(
            {
                "id": n["id"],
                "code": n["code"],
                "name": n["name"],
                "sector": n["sector"],
                "subSector": n.get("subSector"),
                "market": n.get("market"),
                "marketCap": n["marketCap"],
                "capital": n["capital"],
                "degree": n.get("degree", 0),
                "logo": n["logo"],
                "logoText": n.get("logoText"),
            }
        )
    if master:
        m_cache = market_cache or {}
        for code, rec in sorted(master.items()):
            if code in seen_codes:
                continue
            m_info = m_cache.get(code, {})
            index_entries.append(
                {
                    "id": code,
                    "code": code,
                    "name": rec.name,
                    "sector": rec.sector,
                    "subSector": rec.sector33 or None,
                    "market": rec.market or None,
                    "marketCap": round(float(m_info.get("marketCap") or rec.market_cap_trillion or 0.0), 4),
                    "capital": round(float(rec.capital_trillion or 0.0), 4),
                    "degree": 0,
                    "logo": logo_url(code),
                    "logoText": _make_logo_text(rec.name, code),
                }
            )

    # 2) 初期表示用コアネットワーク（core.json：時価総額上位2/3＋構造ハブ上位1/3のハイブリッド選定）
    core_nodes = select_core_nodes(nodes, core_max_nodes=core_max_nodes)
    core_ids = {n["id"] for n in core_nodes}
    core_links = [l for l in links if l["source"] in core_ids and l["target"] in core_ids]

    # 3) 詳細テキストの10分割バケット（details/{0-9,other}.json）
    buckets: dict[str, dict[str, dict]] = defaultdict(dict)
    for code, entry in details_map.items():
        bucket = code[0] if code and code[0].isdigit() else "other"
        buckets[bucket][code] = entry
    for b_key, payload in buckets.items():
        (details_dir / f"{b_key}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    # 4) 決算期集計とメタ情報（meta.json）
    fy_counts = Counter(n.get("fiscalYear") for n in nodes if n.get("fiscalYear"))
    top_fy = fy_counts.most_common(1)[0][0] if fy_counts else ""
    meta = {
        "dataType": "live",
        "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "fiscalYear": top_fy,
        "nodeCount": len(nodes),
        "indexCount": len(index_entries),
        "linkCount": len(links),
        "coreNodeCount": len(core_nodes),
        "coreLinkCount": len(core_links),
    }

    (split_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (split_dir / "index.json").write_text(
        json.dumps(index_entries, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (split_dir / "core.json").write_text(
        json.dumps({"nodes": core_nodes, "links": core_links}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (split_dir / "network.json").write_text(
        json.dumps({"nodes": nodes, "links": links}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return meta


def load_filings_from_paths(input_path: Path | None, extracted_dir: Path | None) -> list[dict]:
    filings: list[dict] = []
    if input_path and input_path.exists():
        raw = json.loads(input_path.read_text(encoding="utf-8"))
        filings.extend(raw if isinstance(raw, list) else [raw])
    if extracted_dir and extracted_dir.exists():
        for p in sorted(extracted_dir.glob("*.json")):
            try:
                item = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(item, list):
                    filings.extend(item)
                elif isinstance(item, dict):
                    filings.append(item)
            except Exception as exc:  # noqa: BLE001
                print(f"[build_network] {p} の読込をスキップ: {exc}", file=sys.stderr)
    return filings


def sample_network() -> dict:
    """src/data/sample.json と同一のサンプルネットワークを返す。"""
    sample_path = Path(__file__).resolve().parent.parent / "src" / "data" / "sample.json"
    return json.loads(sample_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ネットワークJSONの整形（単一／分割静的配信）")
    parser.add_argument("--input", type=Path, default=None, help="抽出結果JSON（単体/配列）")
    parser.add_argument("--extracted-dir", type=Path, default=None, help="抽出結果JSON群のディレクトリ")
    parser.add_argument("--master", type=Path, default=None, help="企業マスタJSON（company_master.json）")
    parser.add_argument("--market-cache", type=Path, default=None, help="時価総額キャッシュJSON")
    parser.add_argument("--aliases", type=Path, default=Path("data/alias_overrides.json"), help="名寄せ例外辞書JSON")
    parser.add_argument("--llm-relations", type=Path, default=Path("data/llm_relations.json"), help="AI Pro抽出関係JSON")
    parser.add_argument("--unmatched-out", type=Path, default=None, help="未一致企業名ログの出力先")
    parser.add_argument("--sample", action="store_true", help="サンプルデータを出力する")
    parser.add_argument("--out", type=Path, default=None, help="単一JSONの出力先")
    parser.add_argument("--split-dir", type=Path, default=None, help="分割静的JSONの出力先ディレクトリ（例: public/data）")
    parser.add_argument("--core-max-nodes", type=int, default=180, help="core.json に含める最大ノード数")
    args = parser.parse_args(argv)

    if args.sample:
        network = sample_network()
        out = args.out or Path("network.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(network, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"nodes={len(network['nodes'])} links={len(network['links'])} -> {out}")
        return 0

    filings = load_filings_from_paths(args.input, args.extracted_dir)
    if not filings:
        parser.error("--input / --extracted-dir / --sample のいずれかを指定してください。")

    master = load_company_master(args.master) if args.master and args.master.exists() else None
    market_cache = load_market_cache(args.market_cache) if args.market_cache and args.market_cache.exists() else None
    aliases = (
        json.loads(args.aliases.read_text(encoding="utf-8"))
        if args.aliases and args.aliases.exists()
        else None
    )
    llm_relations = (
        json.loads(args.llm_relations.read_text(encoding="utf-8"))
        if args.llm_relations and args.llm_relations.exists()
        else None
    )

    network, details_map = build_from_extracted(
        filings,
        master=master,
        market_cache=market_cache,
        alias_overrides=aliases if isinstance(aliases, dict) else None,
        llm_relations=llm_relations if isinstance(llm_relations, list) else None,
        unmatched_out=args.unmatched_out,
    )

    if args.split_dir:
        meta = write_split_bundle(
            network,
            details_map,
            args.split_dir,
            master=master,
            market_cache=market_cache,
            core_max_nodes=args.core_max_nodes,
        )
        print(
            f"分割JSON出力完了 -> {args.split_dir} "
            f"(nodes={meta['nodeCount']}, links={meta['linkCount']}, core={meta['coreNodeCount']}, index={meta['indexCount']})"
        )
    else:
        out = args.out or Path("network.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(network, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"nodes={len(network['nodes'])} links={len(network['links'])} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
