import os, json, time, logging, datetime as dt, uuid, base64, random
from typing import List, Dict, Any, Optional, Tuple, Iterable

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, PlainTextResponse
from pydantic import BaseModel, Field, ConfigDict

from dotenv import load_dotenv

from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Dimension, Metric, OrderBy
)

# ------------------------------ Config ---------------------------------------
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("dashmarketing")

def _dumps(obj) -> bytes:
    """Serializa a JSON (bytes) sin orjson."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

app = FastAPI(title="Dash Marketing API", version="1.2.1")

ENV = os.getenv("ENV", "development")
allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if not allowed_origins:
    allowed_origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "279889272")
GA4_JSON_KEY_PATH = os.getenv("GA4_JSON_KEY_PATH", "/etc/secrets/ga4-credentials.json")
GA4_JSON_KEY_BASE64 = os.getenv("GA4_JSON_KEY_BASE64")
MIN_START_DATE = dt.date(2024, 1, 1)
GA4_TIMEOUT_SECONDS = float(os.getenv("GA4_TIMEOUT_SECONDS", "60"))
GA4_MAX_RETRIES = int(os.getenv("GA4_MAX_RETRIES", "3"))

# ------------------------------ Utils ----------------------------------------
def _ga4_client() -> BetaAnalyticsDataClient:
    info: Dict[str, Any]
    if GA4_JSON_KEY_BASE64:
        info = json.loads(base64.b64decode(GA4_JSON_KEY_BASE64))
    else:
        path = GA4_JSON_KEY_PATH or "/etc/secrets/ga4-credentials.json"
        if not os.path.exists(path):
            path = "/etc/secrets/ga4-credentials.json"
        if not os.path.exists(path):
            raise HTTPException(status_code=500, detail=f"GA4 credentials not found at {path}")
        with open(path, "r") as fh:
            info = json.load(fh)
    creds = service_account.Credentials.from_service_account_info(info)
    return BetaAnalyticsDataClient(credentials=creds)

def _parse_date(s: str) -> dt.date:
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {s}. Expected YYYY-MM-DD")

def _clamp_dates(start: str, end: str) -> Tuple[str, str]:
    s = max(_parse_date(start), MIN_START_DATE)
    e_req = _parse_date(end)
    e = min(e_req, dt.date.today() - dt.timedelta(days=1))
    if s > e:
        raise HTTPException(status_code=400, detail=f"Invalid range after clamp: {s} > {e}")
    return s.isoformat(), e.isoformat()

def _dims() -> List[Dimension]:
    return [
        Dimension(name="date"),
        Dimension(name="country"),
        Dimension(name="city"),
        Dimension(name="deviceCategory"),
        Dimension(name="pagePath"),
        Dimension(name="sessionSource"),
        Dimension(name="sessionMedium"),
        Dimension(name="sessionCampaignName"),
    ]

def _mets() -> List[Metric]:
    return [
        Metric(name="activeUsers"),
        Metric(name="newUsers"),
        Metric(name="sessions"),
        Metric(name="screenPageViews"),
        Metric(name="engagementRate"),
        Metric(name="bounceRate"),
        Metric(name="averageSessionDuration"),
        Metric(name="conversions"),
        Metric(name="totalRevenue"),
    ]

def _stable_order() -> List[OrderBy]:
    return [OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name=d.name)) for d in _dims()]

def _month_range_iter(start: dt.date, end: dt.date) -> List[dt.date]:
    cur = start.replace(day=1)
    out = []
    while cur <= end:
        out.append(cur)
        year = cur.year + (cur.month // 12)
        month = (cur.month % 12) + 1
        cur = dt.date(year, month, 1)
    return out

def _agg_totals(client: BetaAnalyticsDataClient, start_iso: str, end_iso: str) -> Dict[str, float]:
    names = ["sessions", "activeUsers", "screenPageViews", "conversions", "totalRevenue"]
    req = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        date_ranges=[DateRange(start_date=start_iso, end_date=end_iso)],
        dimensions=[],
        metrics=[Metric(name=m) for m in names],
        limit=1,
    )
    resp = client.run_report(req)
    out = {m: 0.0 for m in names}
    if resp.rows:
        mv = resp.rows[0].metric_values
        for i, m in enumerate(names):
            val = mv[i].value
            out[m] = float(val) if (val is not None and val != "") else 0.0
    return out

# -------------------------- Streaming helpers --------------------------------
def _row_to_dict(row, dims: List[Dimension], mets: List[Metric]) -> Dict[str, Any]:
    d = {dims[i].name: row.dimension_values[i].value for i in range(len(dims))}
    for j in range(len(mets)):
        val = row.metric_values[j].value
        d[mets[j].name] = float(val) if (val is not None and val != "") else None
    return d

def _pct_diff(a: float, b: float) -> float:
    return 0.0 if (b or 0.0) == 0.0 else (a - b) / b


# ------------------------------ Middleware -----------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    rid = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.time()
    request.state.request_id = rid
    response = await call_next(request)
    duration = time.time() - start
    log.info("%s %s %s %d %.3fs", rid, request.method, request.url.path, response.status_code, duration)
    response.headers["X-Request-ID"] = rid
    return response


# ------------------------------ Retries --------------------------------------
def _run_report(client: BetaAnalyticsDataClient, req: RunReportRequest, request_id: str):
    for attempt in range(1, GA4_MAX_RETRIES + 1):
        try:
            resp = client.run_report(req, timeout=GA4_TIMEOUT_SECONDS)
            log.info(json.dumps({"attempt": attempt, "status": 200, "sleep_ms": 0, "request_id": request_id}))
            return resp
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "code", None)
            retry_after = None
            if hasattr(exc, "response") and getattr(exc, "response", None):
                retry_after = exc.response.headers.get("Retry-After")
            sleep = min(2 ** (attempt - 1) + random.random(), 60)
            if retry_after:
                try:
                    sleep = max(sleep, float(retry_after))
                except ValueError:
                    pass
            log.info(json.dumps({"attempt": attempt, "status": status, "sleep_ms": int(sleep * 1000), "request_id": request_id}))
            if attempt == GA4_MAX_RETRIES:
                raise
            time.sleep(sleep)

# ------------------------------ Endpoints ------------------------------------
@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "Dash Marketing API is up. See /docs for OpenAPI."

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/version")
def version():
    return {"version": app.version}

class ExportarParams(BaseModel):
    start: str = Field(..., description="YYYY-MM-DD", alias="from")
    end: str = Field(..., description="YYYY-MM-DD", alias="to")
    page_size: int = Field(1000, ge=1, alias="pageSize", description="Rows per page")
    max_pages: int = Field(10000, ge=1, alias="maxPages", description="Safety cap")

    model_config = ConfigDict(populate_by_name=True)


@app.get("/exportar")
def exportar_datos(request: Request, params: ExportarParams = Depends()):
    """Exporta con streaming para no usar memoria: emite {"rows":[ ... ], meta...}"""
    s_iso, e_iso = _clamp_dates(params.start, params.end)
    page_size = params.page_size
    max_pages = params.max_pages
    rid = getattr(request.state, "request_id", str(uuid.uuid4()))
    log.info(f"/exportar start={s_iso} end={e_iso} page_size={page_size} max_pages={max_pages}")

    client = _ga4_client()
    dims = _dims()
    mets = _mets()


    effective_limit = min(page_size, 100000)
    req = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        date_ranges=[DateRange(start_date=s_iso, end_date=e_iso)],
        dimensions=dims,
        metrics=mets,
        order_bys=_stable_order(),
        limit=effective_limit,
    )

    pages = 0
    total_rows_reported: Optional[int] = None
    sum_sessions = sum_users = sum_views = sum_conv = sum_rev = 0.0

    def _gen() -> Iterable[bytes]:
        nonlocal pages, total_rows_reported, sum_sessions, sum_users, sum_views, sum_conv, sum_rev
        yield b'{"rows":['
        first = True
        page_token: Optional[str] = None
        offset = 0
        while True:
            req.page_token = page_token
            req.offset = offset
            resp = _run_report(client, req, rid)
            if total_rows_reported is None:
                total_rows_reported = getattr(resp, "row_count", None)
            batch_count = 0

            for r in resp.rows:
                d = _row_to_dict(r, dims, mets)
                sum_sessions += d.get("sessions") or 0.0
                sum_users    += d.get("activeUsers") or 0.0
                sum_views    += d.get("screenPageViews") or 0.0
                sum_conv     += d.get("conversions") or 0.0
                sum_rev      += d.get("totalRevenue") or 0.0

                if not first:
                    yield b","
                else:
                    first = False
                yield _dumps(d)
                batch_count += 1

            if batch_count == 0:
                break

            pages += 1
            page_token = getattr(resp, "next_page_token", None)
            if page_token:
                offset = 0
            else:
                offset += batch_count
            if (total_rows_reported is not None and offset >= total_rows_reported) or pages >= max_pages:
                if pages >= max_pages:
                    log.warning("Reached max_pages cap; streaming will end early.")
                break

            time.sleep(0.12)

        yield b"],"

        partial = False
        reason: Optional[str] = None
        if pages >= max_pages:
            partial = True
            reason = "max_pages"
        elif total_rows_reported is not None and offset < (total_rows_reported or 0):
            partial = True
            reason = "ga4_limit"

        agg = _agg_totals(client, s_iso, e_iso)
        diff = {
            "sessions": _pct_diff(sum_sessions, agg.get("sessions", 0.0)),
            "activeUsers": _pct_diff(sum_users, agg.get("activeUsers", 0.0)),
            "screenPageViews": _pct_diff(sum_views, agg.get("screenPageViews", 0.0)),
            "conversions": _pct_diff(sum_conv, agg.get("conversions", 0.0)),
            "totalRevenue": _pct_diff(sum_rev, agg.get("totalRevenue", 0.0)),
        }
        body_tail = {
            "rowCount": total_rows_reported,
            "start": s_iso,
            "end": e_iso,
            "pages": pages,
            "partial": partial,
            "audit": {
                "detail_totals": {
                    "sessions": sum_sessions,
                    "activeUsers": sum_users,
                    "screenPageViews": sum_views,
                    "conversions": sum_conv,
                    "totalRevenue": sum_rev,
                },
                "ga4_aggregate": agg,
                "diff_pct": diff,
                "rowCount": total_rows_reported,
                "pages": pages,
                "partial": partial,
            },
        }
        if reason:
            body_tail["reason"] = reason
            body_tail["audit"]["reason"] = reason
        yield _dumps(body_tail)
        yield b"}"

    return StreamingResponse(_gen(), media_type="application/json")

class ExportarMensualParams(BaseModel):
    start: str = Field(..., description="YYYY-MM-DD")
    end: str = Field(..., description="YYYY-MM-DD")
    page_size: int = Field(8000, ge=1000, le=25000)
    sleep_ms: int = Field(120, ge=0, le=2000, description="Backoff ms")


@app.get("/exportar_mensual")
def exportar_mensual(request: Request, params: ExportarMensualParams = Depends()):
    """Streaming por meses para rangos grandes. No acumula filas en memoria."""
    s_iso, e_iso = _clamp_dates(params.start, params.end)
    s = _parse_date(s_iso)
    e = _parse_date(e_iso)
    page_size = params.page_size
    sleep_ms = params.sleep_ms
    rid = getattr(request.state, "request_id", str(uuid.uuid4()))
    log.info(f"/exportar_mensual start={s_iso} end={e_iso} page_size={page_size}")

    client = _ga4_client()
    dims = _dims()
    mets = _mets()

    pages_total = 0
    sum_sessions = sum_users = sum_views = sum_conv = sum_rev = 0.0
    months = _month_range_iter(s, e)

    def _gen() -> Iterable[bytes]:
        nonlocal pages_total, sum_sessions, sum_users, sum_views, sum_conv, sum_rev
        yield b'{"rows":['
        first_row = True

        for m0 in months:
            m_start = m0
            y2 = m0.year + (m0.month // 12)
            m2 = (m0.month % 12) + 1
            m_end = (dt.date(y2, m2, 1) - dt.timedelta(days=1))
            if m_end > e: m_end = e
            if m_start < s: m_start = s
            if m_start > m_end: continue

            effective_limit = min(page_size, 100000)
            req = RunReportRequest(
                property=f"properties/{PROPERTY_ID}",
                date_ranges=[DateRange(start_date=m_start.isoformat(), end_date=m_end.isoformat())],
                dimensions=dims,
                metrics=mets,
                order_bys=_stable_order(),
                limit=effective_limit,
                offset=0,
            )

            while True:
                resp = _run_report(client, req, rid)
                batch_count = 0
                for r in resp.rows:
                    d = _row_to_dict(r, dims, mets)

                    sum_sessions += d.get("sessions") or 0.0
                    sum_users    += d.get("activeUsers") or 0.0
                    sum_views    += d.get("screenPageViews") or 0.0
                    sum_conv     += d.get("conversions") or 0.0
                    sum_rev      += d.get("totalRevenue") or 0.0

                    if not first_row:
                        yield b","
                    else:
                        first_row = False
                    yield _dumps(d)
                    batch_count += 1

                if batch_count == 0:
                    break
                pages_total += 1

                total_month = getattr(resp, "row_count", None)
                if total_month is not None and req.offset + batch_count >= total_month:
                    req.offset += batch_count
                    break

                req.offset += batch_count
                if sleep_ms:
                    time.sleep(sleep_ms / 1000.0)

        yield b"],"

        agg = _agg_totals(client, s_iso, e_iso)
        diff = {
            "sessions": _pct_diff(sum_sessions, agg.get("sessions", 0.0)),
            "activeUsers": _pct_diff(sum_users, agg.get("activeUsers", 0.0)),
            "screenPageViews": _pct_diff(sum_views, agg.get("screenPageViews", 0.0)),
            "conversions": _pct_diff(sum_conv, agg.get("conversions", 0.0)),
            "totalRevenue": _pct_diff(sum_rev, agg.get("totalRevenue", 0.0)),
        }
        tail = {
            "rowCount": None,
            "start": s_iso,
            "end": e_iso,
            "pages": pages_total,
            "partial": False,
            "audit": {
                "detail_totals": {
                    "sessions": sum_sessions,
                    "activeUsers": sum_users,
                    "screenPageViews": sum_views,
                    "conversions": sum_conv,
                    "totalRevenue": sum_rev,
                },
                "ga4_aggregate": agg,
                "diff_pct": diff,
                "rowCount": None,
                "pages": pages_total,
                "partial": False,
            },
        }
        yield _dumps(tail)
        yield b"}"

    return StreamingResponse(_gen(), media_type="application/json")

# ------------------------------ Error handlers --------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    log.exception("Unhandled error")
    return PlainTextResponse(str(exc), status_code=500)

# ------------------------------ Entrypoint ------------------------------------
if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("RENDER_PORT", "8000")))
    log.info(f"Starting server on {host}:{port}")
    uvicorn.run("main:app", host=host, port=port, reload=os.getenv("RELOAD", "false").lower() == "true")
