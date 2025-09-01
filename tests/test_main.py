import os
import datetime as dt
import pytest
import requests
from fastapi.testclient import TestClient
from unittest.mock import patch
from types import SimpleNamespace
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import main

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
        params={"from": start, "to": end, "pageSize": 1000},
        timeout=60,
        proxies={"http": None, "https": None},
    )
    r.raise_for_status()
    data = r.json()
    assert not data.get("partial")
    assert len(data.get("rows", [])) > 0


def test_exportar_many_pages_no_partial() -> None:
    client = TestClient(main.app)

    def fake_run_report(client_param, req, rid):
        if getattr(req, "offset", 0) >= 205:
            return SimpleNamespace(rows=[], row_count=205, next_page_token=None)
        return SimpleNamespace(rows=[object()], row_count=205, next_page_token=None)

    DummyReq = lambda **kwargs: SimpleNamespace(page_token=None, offset=0)

    with patch("main._ga4_client", return_value=None), \
        patch("main._run_report", side_effect=fake_run_report), \
        patch("main._row_to_dict", return_value={"sessions": 1}), \
        patch("main._agg_totals", return_value={"sessions": 205, "activeUsers": 0, "screenPageViews": 0, "conversions": 0, "totalRevenue": 0}), \
        patch("main.time.sleep", return_value=None), \
        patch("main.RunReportRequest", DummyReq), \
        patch("main._dims", return_value=[]), \
        patch("main._mets", return_value=[]):
        r = client.get(
            "/exportar",
            params={
                "from": "2024-01-01",
                "start": "2024-01-01",
                "to": "2024-01-31",
                "end": "2024-01-31",
                "pageSize": 1,
            },
        )
        assert r.status_code == 200
        txt = r.text
        assert txt.count('{"sessions":1}') == 205
        assert '"pages":205' in txt
        assert '"partial":false' in txt
