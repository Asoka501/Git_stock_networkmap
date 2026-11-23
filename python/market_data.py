"""無料ソース（yfinance）による時価総額・公式サイトドメインの差分キャッシュ更新。

全上場企業（約3,800社）の時価総額（兆円）と公式サイトドメイン（LogoKitフォールバック用）を
TTL付きキャッシュで段階更新する。取得失敗時は既存値を保持し、0で上書きしない。

使い方:
    python python/market_data.py update --master data/cache/company_master.json --cache data/cache/market_caps.json --limit 300
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    from company_master import load_company_master
except ImportError:
    from python.company_master import load_company_master  # type: ignore


def extract_domain(url: str | None) -> str:
    if not url:
        return ""
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        host = urlparse(u).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:  # noqa: BLE001
        return ""


def load_market_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_market_cache(cache: dict[str, dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_stale(entry: dict | None, ttl_days: int, now: dt.datetime) -> bool:
    if not entry:
        return True
    updated = entry.get("updatedAt")
    if not updated:
        return True
    try:
        ts = dt.datetime.fromisoformat(updated)
        return (now - ts).days >= ttl_days
    except ValueError:
        return True


def update_market_caps(
    codes: list[str],
    cache_path: Path,
    *,
    limit: int = 300,
    ttl_days: int = 7,
    delay_sec: float = 0.25,
) -> dict[str, dict]:
    """未取得・期限切れの銘柄について yfinance から時価総額（兆円）とドメインを取得する。"""
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        print("[market_data] yfinance が未インストールのため時価総額更新をスキップします。", file=sys.stderr)
        return load_market_cache(cache_path)

    cache = load_market_cache(cache_path)
    now = dt.datetime.now(dt.timezone.utc)
    targets = [c for c in codes if _is_stale(cache.get(c), ttl_days, now)][:limit]

    updated_count = 0
    for code in targets:
        ticker_sym = f"{code}.T"
        prev = cache.get(code, {})
        try:
            tk = yf.Ticker(ticker_sym)
            market_cap_yen = None
            fast = getattr(tk, "fast_info", None)
            if fast is not None:
                market_cap_yen = fast.get("marketCap") or fast.get("market_cap")
            domain = prev.get("domain", "")
            if not market_cap_yen or not domain:
                info = tk.info or {}
                if not market_cap_yen:
                    market_cap_yen = info.get("marketCap")
                if not domain:
                    domain = extract_domain(info.get("website"))

            market_cap_trillion = prev.get("marketCap", 0.0)
            if market_cap_yen and float(market_cap_yen) > 0:
                market_cap_trillion = round(float(market_cap_yen) / 1e12, 4)

            cache[code] = {
                "marketCap": market_cap_trillion,
                "domain": domain,
                "updatedAt": now.isoformat(),
            }
            updated_count += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[market_data] {ticker_sym} 取得失敗（既存値保持）: {exc}", file=sys.stderr)
        if delay_sec > 0:
            time.sleep(delay_sec)

    save_market_cache(cache, cache_path)
    print(f"[market_data] 更新完了: {updated_count}/{len(targets)} 社 (総キャッシュ {len(cache)} 社)", file=sys.stderr)
    return cache


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="yfinance 時価総額・ドメインキャッシュ更新")
    sub = parser.add_subparsers(dest="command", required=True)
    p_upd = sub.add_parser("update", help="未取得・期限切れ銘柄の時価総額を更新する")
    p_upd.add_argument("--master", type=Path, default=Path("data/cache/company_master.json"))
    p_upd.add_argument("--cache", type=Path, default=Path("data/cache/market_caps.json"))
    p_upd.add_argument("--limit", type=int, default=300)
    p_upd.add_argument("--ttl-days", type=int, default=7)

    args = parser.parse_args(argv)
    if args.command == "update":
        master = load_company_master(args.master)
        # プライム市場を優先して更新
        ordered = sorted(
            master.values(),
            key=lambda r: (0 if r.market == "プライム" else 1 if r.market == "スタンダード" else 2, r.code),
        )
        update_market_caps([r.code for r in ordered], args.cache, limit=args.limit, ttl_days=args.ttl_days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
