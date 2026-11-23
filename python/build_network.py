"""抽出結果をフロントエンド用ネットワークJSONへ整形する。

入力: parse_xbrl.py が出力した抽出結果JSON（単体または配列）
出力: network.json（スキーマは README の「ネットワークJSONスキーマ」を参照）

使い方:
    python build_network.py --input extracted.json --out network.json
    python build_network.py --sample --out network.json   # サンプルデータを出力

サンプルデータは src/data/sample.json と同一内容で、フロントエンド単体でも
動作確認できるようにするためのもの。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# 関係値（value）への換算係数: 売上割合(%) をそのまま基礎値にし、上限100で丸める
def _value_from_ratio(ratio_percent: float | None) -> int:
    if ratio_percent is None:
        return 50  # 割合不明の関係は中位として扱う（TODO: 金額ベースの換算を検討）
    return max(5, min(100, round(ratio_percent * 5)))


def logo_url(code: str) -> str:
    return f"https://logoapi.sh/ticker/{code}.svg"


def build_from_extracted(filings: list[dict]) -> dict:
    """抽出結果の配列からネットワークJSONを組み立てる。"""
    nodes: dict[str, dict] = {}
    links: list[dict] = []

    def ensure_node(code: str, name: str, sector: str = "その他") -> None:
        if code and code not in nodes:
            nodes[code] = {
                "id": code,
                "code": code,
                "name": name,
                "sector": sector,
                # TODO: 時価総額・資本金は外部データ（企業マスタ等）と突合せて埋める
                "marketCap": 0,
                "capital": 0,
                "logo": logo_url(code),
            }

    for filing in filings:
        filer_code = str(filing.get("securities_code") or "")
        ensure_node(filer_code, filing.get("filer_name", ""))
        for rel in filing.get("customers", []):
            # TODO: 相手企業名→証券コードの名寄せ（現状は名称のみで仮ノード化）
            counter_code = str(rel.get("counterparty_code") or "")
            ensure_node(counter_code, rel.get("counterparty", ""))
            if filer_code and counter_code:
                links.append(
                    {
                        "source": counter_code,
                        "target": filer_code,
                        "value": _value_from_ratio(rel.get("ratio_percent")),
                        "kind": "主要顧客・供給",
                        "detail": rel.get("note", ""),
                    }
                )
    return {"nodes": list(nodes.values()), "links": links}


def sample_network() -> dict:
    """src/data/sample.json と同一のサンプルネットワークを返す。"""
    sample_path = Path(__file__).resolve().parent.parent / "src" / "data" / "sample.json"
    return json.loads(sample_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ネットワークJSONの整形")
    parser.add_argument("--input", type=Path, default=None, help="抽出結果JSON（単体/配列）")
    parser.add_argument("--sample", action="store_true", help="サンプルデータを出力する")
    parser.add_argument("--out", type=Path, default=Path("network.json"), help="出力先")
    args = parser.parse_args(argv)

    if args.sample:
        network = sample_network()
    elif args.input:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        filings = raw if isinstance(raw, list) else [raw]
        network = build_from_extracted(filings)
    else:
        parser.error("--input か --sample のどちらかを指定してください。")

    args.out.write_text(json.dumps(network, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"nodes={len(network['nodes'])} links={len(network['links'])} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
