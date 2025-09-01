import os
import datetime as dt
import pytest
import requests

BASE_URL = os.getenv("BASE_URL", "https://dashmarketing.onrender.com").rstrip("/")


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # ignora proxies del sistema
    return s


@pytest.mark.e2e
def test_exportar() -> None:
    s = _session()
    r = s.get(f"{BASE_URL}/health", timeout=10, proxies={"http": None, "https": None})
    r.raise_for_status()
    assert r.json() == {"status": "ok"}

    start = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    end = dt.date.today().isoformat()

    r = s.get(
        f"{BASE_URL}/exportar",
        params={"from": start, "to": end, "pageSize": 1000, "maxPages": 10000},
        timeout=60,
        proxies={"http": None, "https": None},
    )
    r.raise_for_status()
    data = r.json()
    assert not data.get("partial")
    assert len(data.get("rows", [])) > 0
