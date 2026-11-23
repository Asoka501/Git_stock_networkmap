"""EDINET API v2 クライアント。

有価証券報告書などの開示書類一覧の取得と、XBRL（ZIP）のダウンロードを行う。

使い方:
    export EDINET_API_KEY="<EDINETで発行したSubscription-Key>"
    python edinet_client.py list --date 2025-06-27 --out documents.json
    python edinet_client.py download --doc-id S100XXXX --out-dir data/raw

APIキーは環境変数 EDINET_API_KEY からのみ読み取る。コードやリポジトリには
絶対にキーを書き込まないこと。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

BASE_URL = "https://disclosure.edinet-fsa.go.jp/api/v2"


def get_api_key() -> str:
    """環境変数 EDINET_API_KEY から Subscription-Key を取得する。"""
    key = os.environ.get("EDINET_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "環境変数 EDINET_API_KEY が設定されていません。"
            "EDINETで発行した Subscription-Key を export してください。"
        )
    return key


def fetch_documents(date: str, doc_type: int = 2, timeout: int = 60) -> dict:
    """書類一覧API（documents.json）を取得する。

    Args:
        date: 書類の提出日（YYYY-MM-DD）。
        doc_type: 取得情報の種別。2 = 提出書類一覧＋メタデータ（既定）。

    Returns:
        APIレスポンスのJSON（results に書類メタデータの配列が入る）。
    """
    resp = requests.get(
        f"{BASE_URL}/documents.json",
        params={"date": date, "type": doc_type},
        headers={"Subscription-Key": get_api_key()},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def list_filings(date: str) -> list[dict]:
    """指定日に提出された有価証券報告書（書類種別コード 120）のみを返す。"""
    data = fetch_documents(date)
    results = data.get("results") or []
    # docTypeCode "120" = 有価証券報告書
    return [r for r in results if r.get("docTypeCode") == "120" and r.get("xbrlFlag") == "1"]


def download_xbrl(doc_id: str, out_dir: Path, timeout: int = 300) -> Path:
    """書類のXBRL（type=1）をZIPでダウンロードし、保存先パスを返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc_id}.zip"
    if out_path.exists():
        return out_path
    resp = requests.get(
        f"{BASE_URL}/documents/{doc_id}",
        params={"type": 1},
        headers={"Subscription-Key": get_api_key()},
        timeout=timeout,
    )
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EDINET API v2 クライアント")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="指定日の有価証券報告書の一覧を取得する")
    p_list.add_argument("--date", required=True, help="提出日 YYYY-MM-DD")
    p_list.add_argument("--out", type=Path, default=None, help="JSONの保存先（省略時は標準出力）")

    p_dl = sub.add_parser("download", help="書類IDのXBRL ZIPをダウンロードする")
    p_dl.add_argument("--doc-id", required=True, help="書類ID（docID）")
    p_dl.add_argument("--out-dir", type=Path, default=Path("data/raw"), help="保存先ディレクトリ")

    args = parser.parse_args(argv)

    if args.command == "list":
        filings = list_filings(args.date)
        text = json.dumps(filings, ensure_ascii=False, indent=2)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
            print(f"{len(filings)} 件の有価証券報告書を {args.out} に保存しました。", file=sys.stderr)
        else:
            print(text)
    elif args.command == "download":
        path = download_xbrl(args.doc_id, args.out_dir)
        print(f"ダウンロードしました: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
