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

## Common issues
- CORS 4xx: check `ALLOWED_ORIGINS`.
- 413 payload: result too large, lower `pageSize`.
- 502/504: upstream timeout; view GA4 quotas.
- Cold starts on free plan may exceed 50s.
- Large exports stream until GA4 `rowCount` is exhausted. Use `maxPages` query
  parameter to set an optional safety cap.

## Corporate proxy / 403 tunnel
If you see `ProxyError('Tunnel connection failed: 403 Forbidden')` when running probes:
1. Export NO_PROXY to bypass the system proxy:
   - Bash/macOS: `export NO_PROXY=localhost,127.0.0.1,.onrender.com`
   - PowerShell: `$env:NO_PROXY="localhost,127.0.0.1,.onrender.com"`
2. Or simply run the probes (they already ignore proxies in code):
   `python scripts/prod_probe.py`
