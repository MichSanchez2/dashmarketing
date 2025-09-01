import pathlib, sys
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Dimension, Metric, FilterExpression, Filter

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from main import _agg_totals


class _FakeMetricValue:
    def __init__(self, value):
        self.value = str(value)


class _FakeRow:
    def __init__(self, values):
        self.metric_values = [_FakeMetricValue(v) for v in values]


class _FakeResp:
    def __init__(self, values):
        self.rows = [_FakeRow(values)]


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.last_req = None

    def run_report(self, req, timeout=None):
        self.last_req = req
        return self._resp


def test_agg_totals_matches_detail_sum():
    detail_rows = [
        {"sessions": 10, "activeUsers": 5},
        {"sessions": 20, "activeUsers": 7},
    ]
    totals = {
        "sessions": sum(r["sessions"] for r in detail_rows),
        "activeUsers": sum(r["activeUsers"] for r in detail_rows),
        "screenPageViews": 0.0,
        "conversions": 0.0,
        "totalRevenue": 0.0,
    }

    resp = _FakeResp([totals[m] for m in [
        "sessions",
        "activeUsers",
        "screenPageViews",
        "conversions",
        "totalRevenue",
    ]])
    client = _FakeClient(resp)

    detail_req = RunReportRequest(
        property="properties/123",
        date_ranges=[DateRange(start_date="2024-01-01", end_date="2024-01-31")],
        dimensions=[Dimension(name="city")],
        metrics=[Metric(name="sessions"), Metric(name="activeUsers")],
        dimension_filter=FilterExpression(
            filter=Filter(field_name="city", string_filter=Filter.StringFilter(value="London"))
        ),
    )

    agg = _agg_totals(client, detail_req)
    assert agg == totals
    assert client.last_req.dimension_filter == detail_req.dimension_filter
    assert client.last_req.date_ranges == detail_req.date_ranges
    assert client.last_req.dimensions == []
