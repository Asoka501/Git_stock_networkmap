"""EDINET API 接続確認スクリプト。

APIキーの発行後に、ローカル環境から EDINET API へ接続できるかを
最小リクエストで確認する。キーは環境変数 EDINET_API_KEY からのみ読み、
値そのものは表示しない。

使い方:
    export EDINET_API_KEY=<発行した Subscription-Key>
    python check_api.py [--date 2024-05-01]

終了コード: 0 = 接続成功 / 1 = キー未設定 / 2 = 認証エラー / 3 = その他
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

BASE_URL = "https://disclosure.edinet-fsa.go.jp/api/v2"


def main() -> int:
    parser = argparse.ArgumentParser(description="EDINET API 接続確認")
    parser.add_argument("--date", default="2024-05-01", help="書類一覧を取得する日付 (YYYY-MM-DD)")
    args = parser.parse_args()

    key = os.environ.get("EDINET_API_KEY", "").strip()
    if not key:
        print("NG: 環境変数 EDINET_API_KEY が設定されていません。")
        print("    EDINET閲覧サイトでアカウントを作成し、APIキーを発行してください。")
        return 1

    try:
        resp = requests.get(
            f"{BASE_URL}/documents.json",
            params={"date": args.date, "type": 2},
            headers={"Subscription-Key": key},
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"NG: 通信エラー ({exc.__class__.__name__})")
        return 3

    if resp.status_code == 200:
        data = resp.json()
        results = data.get("results") or []
        print(f"OK: 接続成功 (date={args.date}, 書類 {len(results)} 件)")
        return 0
    if resp.status_code in (401, 403):
        print(f"NG: 認証エラー (HTTP {resp.status_code})。APIキーの値を確認してください。")
        return 2
    print(f"NG: 予期しない応答 (HTTP {resp.status_code})")
    return 3


if __name__ == "__main__":
    sys.exit(main())
