import os
import sys
import time
import datetime as dt
import requests
import json

BASE_URL = os.environ.get("BASE_URL", "https://dashmarketing.onrender.com").rstrip("/")
NO_PROXY_HOSTS = os.environ.get("NO_PROXY", "")
DEFAULT_NO_PROXY = "localhost,127.0.0.1,.onrender.com"
if DEFAULT_NO_PROXY not in NO_PROXY_HOSTS:
    os.environ["NO_PROXY"] = (NO_PROXY_HOSTS + "," if NO_PROXY_HOSTS else "") + DEFAULT_NO_PROXY


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # ignora proxies del sistema
    return s


def main() -> int:
    start = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    end = dt.date.today().isoformat()
    t0 = time.time()
    s = _session()
    resp = s.get(f"{BASE_URL}/health", timeout=10, proxies={"http": None, "https": None})
    if resp.status_code != 200 or resp.json() != {"status": "ok"}:
        print("health check failed", file=sys.stderr)
        return 1
    resp = s.get(
        f"{BASE_URL}/exportar",
        params={"from": start, "to": end, "pageSize": 1000},
        timeout=60,
        proxies={"http": None, "https": None},
    )
    duration = time.time() - t0
    if resp.status_code != 200:
        print("exportar status", resp.status_code, file=sys.stderr)
        return 1
    data = resp.json()
    rows = data.get("rows", [])
    if data.get("partial") or not rows:
        print("partial or empty", file=sys.stderr)
        return 1
    print(json.dumps({"rows": len(rows), "duration_ms": int(duration * 1000)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
