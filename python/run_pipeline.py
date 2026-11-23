"""全上場企業EDINETデータ取得・解析・分割JSON生成パイプラインのオーケストレータ。

ローカル実行・GitHub Actions自動バッチの両方から同じコマンドで実行できる。
外部LLM APIキーは不要（EDINET_API_KEY のみで完全無料動作）。

使い方:
    # 直近14日分の新着有報を差分取得→解析→時価総額補完→public/data/ へ分割出力
    export EDINET_API_KEY="<Subscription-Key>"
    python python/run_pipeline.py --days 14 --max-downloads 120 --out-dir public/data

    # 初回バックフィル（例：6月決算集中期を指定して段階取得）
    python python/run_pipeline.py --from 2025-06-20 --to 2025-06-30 --max-downloads 200 --out-dir public/data
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

try:
    from build_network import build_from_extracted, load_filings_from_paths, write_split_bundle
    from company_master import (
        CompanyResolver,
        clean_sec_code,
        load_company_master,
        sync_company_master,
    )
    from edinet_client import (
        DEFAULT_DELAY_SEC,
        download_xbrl,
        load_manifest,
        save_manifest,
        scan_filings_range,
    )
    from market_data import load_market_cache, update_market_caps
    from parse_xbrl import parse_zip
except ImportError:
    from python.build_network import build_from_extracted, load_filings_from_paths, write_split_bundle  # type: ignore
    from python.company_master import (  # type: ignore
        CompanyResolver,
        clean_sec_code,
        load_company_master,
        sync_company_master,
    )
    from python.edinet_client import (  # type: ignore
        DEFAULT_DELAY_SEC,
        download_xbrl,
        load_manifest,
        save_manifest,
        scan_filings_range,
    )
    from python.market_data import load_market_cache, update_market_caps  # type: ignore
    from python.parse_xbrl import parse_zip  # type: ignore


def run(args: argparse.Namespace) -> int:
    cache_dir: Path = args.cache_dir
    raw_dir: Path = args.raw_dir
    extracted_dir = cache_dir / "extracted"
    master_path = cache_dir / "company_master.json"
    manifest_path = cache_dir / "manifest.json"
    market_cache_path = cache_dir / "market_caps.json"
    unmatched_path = cache_dir / "unmatched_names.json"

    cache_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 企業マスタ同期（JPX + EDINETコードリスト）
    if not args.skip_master or not master_path.exists():
        print("[Step 1/5] JPX・EDINET企業マスタを同期中...", file=sys.stderr)
        master = sync_company_master(master_path, alias_overrides_path=args.aliases)
    else:
        master = load_company_master(master_path)
    print(f"[Step 1/5] 企業マスタ準備完了: {len(master)} 社", file=sys.stderr)

    aliases = (
        json.loads(args.aliases.read_text(encoding="utf-8"))
        if args.aliases and args.aliases.exists()
        else None
    )
    resolver = CompanyResolver(master, alias_overrides=aliases if isinstance(aliases, dict) else None)

    # Step 2 & 3: EDINET差分取得＋iXBRL解析（処理後ZIPを自動削除して容量節約）
    if not args.skip_edinet:
        to_date = dt.date.fromisoformat(args.to_date) if args.to_date else dt.date.today()
        from_date = (
            dt.date.fromisoformat(args.from_date)
            if args.from_date
            else to_date - dt.timedelta(days=args.days)
        )
        print(f"[Step 2/5] EDINET有報を走査中: {from_date} 〜 {to_date}", file=sys.stderr)
        filings_meta = scan_filings_range(from_date, to_date, listed_only=True)
        manifest = load_manifest(manifest_path)

        processed = 0
        for meta in filings_meta:
            doc_id = str(meta.get("docID") or "")
            if not doc_id:
                continue
            out_json = extracted_dir / f"{doc_id}.json"
            if manifest.get(doc_id, {}).get("parsed") and out_json.exists():
                continue
            if processed >= args.max_downloads:
                break

            try:
                zip_path = download_xbrl(doc_id, raw_dir)
                extracted = parse_zip(zip_path, resolver=resolver, manifest_entry=meta)
                out_json.write_text(
                    json.dumps(asdict(extracted), ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                manifest[doc_id] = {
                    "docID": doc_id,
                    "edinetCode": meta.get("edinetCode"),
                    "secCode": clean_sec_code(meta.get("secCode")) or extracted.securities_code,
                    "filerName": meta.get("filerName") or extracted.filer_name,
                    "periodEnd": meta.get("periodEnd"),
                    "submitDateTime": meta.get("submitDateTime"),
                    "parsed": True,
                }
                processed += 1
                if not args.keep_zips and zip_path.exists():
                    zip_path.unlink()
                time.sleep(DEFAULT_DELAY_SEC)
            except Exception as exc:  # noqa: BLE001
                print(f"[Step 2/5] {doc_id} の処理に失敗（スキップ）: {exc}", file=sys.stderr)

        save_manifest(manifest, manifest_path)
        print(f"[Step 3/5] 有報パース完了: 今回新規 {processed} 件 / 累計キャッシュ {len(list(extracted_dir.glob('*.json')))} 件", file=sys.stderr)

    all_filings = load_filings_from_paths(None, extracted_dir)
    if not all_filings:
        print("[Step 5/5] 抽出済み有報JSONが0件のため public/data/ の書き出しをスキップします（サンプルフォールバック維持）。", file=sys.stderr)
        return 0

    llm_relations = (
        json.loads(args.llm_relations.read_text(encoding="utf-8"))
        if args.llm_relations and args.llm_relations.exists()
        else None
    )

    # 先にネットワーク上の出現ノード（次数上位）を特定し、表示対象企業から優先的に時価総額・ドメインを更新する
    prelim_network, _ = build_from_extracted(
        all_filings,
        master=master,
        market_cache=load_market_cache(market_cache_path),
        alias_overrides=aliases if isinstance(aliases, dict) else None,
        llm_relations=llm_relations if isinstance(llm_relations, list) else None,
    )
    priority_codes = [n["code"] for n in prelim_network["nodes"]]
    priority_set = set(priority_codes)
    rest_codes = [
        r.code
        for r in sorted(
            master.values(),
            key=lambda r: (0 if r.market == "プライム" else 1 if r.market == "スタンダード" else 2, -r.capital_trillion, r.code),
        )
        if r.code not in priority_set
    ]
    ordered_codes = priority_codes + rest_codes

    # Step 4: 時価総額＆公式サイトドメイン補完（ネットワーク出現企業を最優先）
    if not args.skip_market_cap:
        print(
            f"[Step 4/5] 時価総額・ドメインを更新中（ネットワーク上位 {len(priority_codes)} 社を優先、最大 {args.market_cap_limit} 社）...",
            file=sys.stderr,
        )
        market_cache = update_market_caps(
            ordered_codes,
            market_cache_path,
            limit=args.market_cap_limit,
            ttl_days=args.market_cap_ttl_days,
        )
    else:
        market_cache = load_market_cache(market_cache_path)

    # Step 5: 最新時価総額・ドメインを反映して分割静的JSON生成（public/data/）
    network, details_map = build_from_extracted(
        all_filings,
        master=master,
        market_cache=market_cache,
        alias_overrides=aliases if isinstance(aliases, dict) else None,
        llm_relations=llm_relations if isinstance(llm_relations, list) else None,
        unmatched_out=unmatched_path,
    )
    meta = write_split_bundle(
        network,
        details_map,
        args.out_dir,
        master=master,
        market_cache=market_cache,
        core_max_nodes=args.core_max_nodes,
    )
    print(
        f"[Step 5/5] 完了! {args.out_dir} -> "
        f"nodes={meta['nodeCount']} links={meta['linkCount']} core={meta['coreNodeCount']} index={meta['indexCount']}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EDINET 全上場ネットワーク更新パイプライン")
    parser.add_argument("--from", dest="from_date", default=None, help="走査開始日 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", default=None, help="走査終了日 YYYY-MM-DD（既定: 今日）")
    parser.add_argument("--days", type=int, default=14, help="--from 未指定時の遡り日数（既定: 14日）")
    parser.add_argument("--max-downloads", type=int, default=120, help="1回の実行で取得・解析する最大件数")
    parser.add_argument("--keep-zips", action="store_true", help="解析後に生XBRL ZIPを削除せず残す")
    parser.add_argument("--skip-master", action="store_true", help="既存マスタがある場合、JPX/EDINET同期をスキップ")
    parser.add_argument("--skip-edinet", action="store_true", help="EDINET取得をスキップし、既存キャッシュからJSON再構築のみ行う")
    parser.add_argument("--skip-market-cap", action="store_true", help="yfinance時価総額更新をスキップ")
    parser.add_argument("--market-cap-limit", type=int, default=250, help="1回で更新する時価総額の最大社数")
    parser.add_argument("--market-cap-ttl-days", type=int, default=7, help="時価総額キャッシュの有効日数")
    parser.add_argument("--core-max-nodes", type=int, default=180, help="初期表示 core.json の最大ノード数")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--aliases", type=Path, default=Path("data/alias_overrides.json"))
    parser.add_argument("--llm-relations", type=Path, default=Path("data/llm_relations.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("public/data"))
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
