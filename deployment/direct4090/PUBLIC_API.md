# Physical phones: direct 4090 public API

The existing API stays on `127.0.0.1:18126`. The public entry runs on
`0.0.0.0:6008` in the same 4090 instance and streams requests and responses
to that API. The phone uses AutoDL's HTTPS mapping for port 6008:

```text
iPhone -> AutoDL HTTPS ingress -> 4090 port 6008 -> 4090 API port 18126
```

Mac, Windows and the Germany VPS are outside this application data path.
The Mac simulator may continue using its private SSH forward independently.

Copy `public_api_proxy.py` and `supervisor-public-api.conf` into
`/root/autodl-tmp/adaptive-book-ocr/ops/`, then run:

```bash
/root/miniconda3/bin/supervisorctl -c /root/autodl-tmp/adaptive-book-ocr/supervisord.conf reread
/root/miniconda3/bin/supervisorctl -c /root/autodl-tmp/adaptive-book-ocr/supervisord.conf update cloudpath-public-api
curl -f http://127.0.0.1:6008/api/health
```

Reuse the existing API virtual environment, which provides FastAPI, HTTPX and
Uvicorn. The proxy streams uploads and downloads, preserves response headers
including separate session cookies, and allows long model requests. The
private development endpoint `/api/native/local-login` is blocked on the public
entry. Phones log in through the existing email verification flow.

In AutoDL's console for this instance, select the custom service for port
6008 using HTTP protocol, then copy its HTTPS URL. That URL is allocated by
AutoDL; it cannot be derived from the SSH hostname and port. Verify
`<public-url>/api/health` externally before providing it to users. Enter only
the origin (HTTPS hostname and port) in the App's learning service address.

Server-side verification on 2026-11-06 passed health, blocked local login,
unauthorized private export, configured tester email login, Secure session
cookies, authenticated library access, and complete PDF/figure SHA-256 checks.
The tested PDF contained 28,238,825 bytes. Public HTTPS verification remains
pending until the assigned AutoDL URL is available.
