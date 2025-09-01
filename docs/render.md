# Render deployment

## Environment variables
- `ENV` – set to `production`
- `ALLOWED_ORIGINS` – comma separated list of allowed CORS origins
- `GA4_PROPERTY_ID`
- `GA4_JSON_KEY_PATH` or `GA4_JSON_KEY_BASE64`
- `GA4_TIMEOUT_SECONDS`
- `GA4_MAX_RETRIES`
- `GOOGLE_ADS_YAML_PATH`

## Start command
```
uvicorn main:app --host 0.0.0.0 --port $PORT --workers 2
```

## Logs
Use the Render dashboard or `render logs` CLI. Each request includes `X-Request-ID`.

## Partial responses
The `exportar` endpoint may return a JSON response with a boolean `partial` flag.
Clients must verify that `partial` is `false` before using the data and should
retry later if it is `true`. See `powerbi_m_example.m` for an example of failing
early when a partial response is encountered.

## Common issues
- CORS 4xx: check `ALLOWED_ORIGINS`.
- 413 payload: result too large, lower `pageSize`.
- 502/504: upstream timeout; view GA4 quotas.
- Cold starts on free plan may exceed 50s.

## Corporate proxy / 403 tunnel
If you see `ProxyError('Tunnel connection failed: 403 Forbidden')` when running probes:
1. Export NO_PROXY to bypass the system proxy:
   - Bash/macOS: `export NO_PROXY=localhost,127.0.0.1,.onrender.com`
   - PowerShell: `$env:NO_PROXY="localhost,127.0.0.1,.onrender.com"`
2. Or simply run the probes (they already ignore proxies in code):
   `python scripts/prod_probe.py`
