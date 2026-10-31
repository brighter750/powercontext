"""等待 PowerContext Server 就绪。

``powercontext server run`` 是官方的启动方式；本脚本只负责在它就绪之前轮询，
便于自动化脚本使用。

用法::

    python minimax/scripts/wait_for_powercontext.py --url http://127.0.0.1:8000 --timeout 120
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _probe(url: str) -> tuple[bool, str]:
    for path in ("/health/live", "/v1/server-info"):
        try:
            with urllib.request.urlopen(f"{url}{path}", timeout=5) as response:
                if response.status < 400:
                    body = response.read().decode("utf-8", errors="replace")
                    return True, body[:400]
        except urllib.error.HTTPError as error:
            return False, f"HTTP {error.code}"
        except Exception as error:  # noqa: BLE001
            return False, f"{type(error).__name__}: {error}"
    return False, "unreachable"


def main() -> int:
    parser = argparse.ArgumentParser(description="等待 PowerContext Server 就绪")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    url = args.url.rstrip("/")
    deadline = time.monotonic() + args.timeout
    last = ""
    while time.monotonic() < deadline:
        ok, detail = _probe(url)
        if ok:
            print(json.dumps({"url": url, "ready": True, "detail": detail}, ensure_ascii=False))
            return 0
        last = detail
        time.sleep(args.interval)
    print(json.dumps({"url": url, "ready": False, "detail": last}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
