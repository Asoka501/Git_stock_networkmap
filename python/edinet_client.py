"""EDINET API v2 クライアント（単発取得＋全上場向け差分バッチ同期）。

有価証券報告書などの開示書類一覧の取得と、XBRL（ZIP）のダウンロードを行う。
全上場企業規模（約3,800社）に対応するため、提出日範囲を走査して上場企業の最新有報のみを
マニフェスト（docID単位）で差分管理し、既処理書類の再ダウンロードを防ぐ。

使い方:
    export EDINET_API_KEY="<EDINETで発行したSubscription-Key>"
    python python/edinet_client.py list --date 2025-06-27 --out documents.json
    python python/edinet_client.py download --doc-id S100XXXX --out-dir data/raw
    python python/edinet_client.py sync --from 2025-06-20 --to 2025-06-30 --max-downloads 100

APIキーは環境変数 EDINET_API_KEY からのみ読み取る。コードやリポジトリには
絶対にキーを書き込まないこと。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests

try:
    from company_master import clean_sec_code
except ImportError:
    from python.company_master import clean_sec_code  # type: ignore

BASE_URL = "https://disclosure.edinet-fsa.go.jp/api/v2"
DEFAULT_DELAY_SEC = 0.35


def get_api_key() -> str:
    """環境変数 EDINET_API_KEY から Subscription-Key を取得する。"""
    key = os.environ.get("EDINET_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "環境変数 EDINET_API_KEY が設定されていません。"
            "EDINETで発行した Subscription-Key を export してください。"
        )
    return key


def _request_with_retry(
    url: str,
    *,
    params: dict,
    timeout: int,
    max_retries: int = 3,
) -> requests.Response:
    headers = {"Subscription-Key": get_api_key()}
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = 2.0 * (attempt + 1)
                print(
                    f"[edinet] HTTP {resp.status_code} を受信、{wait:.1f}秒後に再試行します ({url})",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt + 1 < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError(f"EDINET API リクエストに失敗しました: {url}")


def fetch_documents(date: str, doc_type: int = 2, timeout: int = 60) -> dict:
    """書類一覧API（documents.json）を取得する。

    Args:
        date: 書類の提出日（YYYY-MM-DD）。
        doc_type: 取得情報の種別。2 = 提出書類一覧＋メタデータ（既定）。

    Returns:
        APIレスポンスのJSON（results に書類メタデータの配列が入る）。
    """
    resp = _request_with_retry(
        f"{BASE_URL}/documents.json",
        params={"date": date, "type": doc_type},
        timeout=timeout,
    )
    return resp.json()


def list_filings(date: str, *, listed_only: bool = False) -> list[dict]:
    """指定日に提出された有価証券報告書（書類種別コード 120）のみを返す。"""
    data = fetch_documents(date)
    results = data.get("results") or []
    filings: list[dict] = []
    for r in results:
        if r.get("docTypeCode") != "120" or r.get("xbrlFlag") != "1":
            continue
        if r.get("withdrawalStatus") == "1":
            continue
        if listed_only:
            code = clean_sec_code(r.get("secCode"))
            if not code:
                continue
        filings.append(r)
    return filings


def iter_dates(from_date: dt.date, to_date: dt.date) -> list[dt.date]:
    if from_date > to_date:
        from_date, to_date = to_date, from_date
    days = (to_date - from_date).days
    return [from_date + dt.timedelta(days=offset) for offset in range(days + 1)]


def scan_filings_range(
    from_date: dt.date,
    to_date: dt.date,
    *,
    listed_only: bool = True,
    delay_sec: float = DEFAULT_DELAY_SEC,
) -> list[dict]:
    """指定期間の全提出日を走査し、各社（edinetCode単位）最新の有報メタデータ一覧を返す。"""
    latest_by_filer: dict[str, dict] = {}
    dates = iter_dates(from_date, to_date)
    for idx, d in enumerate(dates):
        # 土日は原則提出がないためスキップしてリクエスト数を節約
        if d.weekday() >= 5:
            continue
        date_str = d.isoformat()
        try:
            filings = list_filings(date_str, listed_only=listed_only)
        except Exception as exc:  # noqa: BLE001
            print(f"[edinet] {date_str} の一覧取得に失敗（スキップ）: {exc}", file=sys.stderr)
            continue

        for r in filings:
            edinet_code = str(r.get("edinetCode") or "")
            if not edinet_code:
                continue
            prev = latest_by_filer.get(edinet_code)
            cur_ts = str(r.get("submitDateTime") or "")
            if prev is None or cur_ts >= str(prev.get("submitDateTime") or ""):
                latest_by_filer[edinet_code] = r

        if idx + 1 < len(dates) and delay_sec > 0:
            time.sleep(delay_sec)

    return sorted(
        latest_by_filer.values(),
        key=lambda r: str(r.get("submitDateTime") or ""),
        reverse=True,
    )


def download_xbrl(doc_id: str, out_dir: Path, timeout: int = 300) -> Path:
    """書類のXBRL（type=1）をZIPでダウンロードし、保存先パスを返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc_id}.zip"
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path
    resp = _request_with_retry(
        f"{BASE_URL}/documents/{doc_id}",
        params={"type": 1},
        timeout=timeout,
    )
    out_path.write_bytes(resp.content)
    return out_path


def load_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_manifest(manifest: dict[str, dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EDINET API v2 クライアント")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="指定日の有価証券報告書の一覧を取得する")
    p_list.add_argument("--date", required=True, help="提出日 YYYY-MM-DD")
    p_list.add_argument("--listed-only", action="store_true", help="上場企業（secCodeあり）のみに絞る")
    p_list.add_argument("--out", type=Path, default=None, help="JSONの保存先（省略時は標準出力）")

    p_dl = sub.add_parser("download", help="書類IDのXBRL ZIPをダウンロードする")
    p_dl.add_argument("--doc-id", required=True, help="書類ID（docID）")
    p_dl.add_argument("--out-dir", type=Path, default=Path("data/raw"), help="保存先ディレクトリ")

    p_sync = sub.add_parser("sync", help="提出日範囲を走査して未処理の上場企業有報ZIPを差分ダウンロードする")
    p_sync.add_argument("--from", dest="from_date", required=True, help="開始日 YYYY-MM-DD")
    p_sync.add_argument("--to", dest="to_date", required=True, help="終了日 YYYY-MM-DD")
    p_sync.add_argument("--out-dir", type=Path, default=Path("data/raw"), help="ZIP保存先ディレクトリ")
    p_sync.add_argument("--manifest", type=Path, default=Path("data/cache/manifest.json"), help="取得済みマニフェスト")
    p_sync.add_argument("--max-downloads", type=int, default=100, help="1回の実行でダウンロードする最大件数")

    args = parser.parse_args(argv)

    if args.command == "list":
        filings = list_filings(args.date, listed_only=args.listed_only)
        text = json.dumps(filings, ensure_ascii=False, indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"{len(filings)} 件の有価証券報告書を {args.out} に保存しました。", file=sys.stderr)
        else:
            print(text)
    elif args.command == "download":
        path = download_xbrl(args.doc_id, args.out_dir)
        print(f"ダウンロードしました: {path}", file=sys.stderr)
    elif args.command == "sync":
        start = dt.date.fromisoformat(args.from_date)
        end = dt.date.fromisoformat(args.to_date)
        filings = scan_filings_range(start, end, listed_only=True)
        manifest = load_manifest(args.manifest)
        downloaded = 0
        for r in filings:
            doc_id = str(r.get("docID") or "")
            if not doc_id or doc_id in manifest:
                continue
            if downloaded >= args.max_downloads:
                break
            download_xbrl(doc_id, args.out_dir)
            manifest[doc_id] = {
                "docID": doc_id,
                "edinetCode": r.get("edinetCode"),
                "secCode": clean_sec_code(r.get("secCode")),
                "filerName": r.get("filerName"),
                "periodEnd": r.get("periodEnd"),
                "submitDateTime": r.get("submitDateTime"),
                "parsed": False,
            }
            downloaded += 1
            time.sleep(DEFAULT_DELAY_SEC)
        save_manifest(manifest, args.manifest)
        print(
            f"走査完了: 候補 {len(filings)} 社 / 新規ダウンロード {downloaded} 件 -> {args.out_dir}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
