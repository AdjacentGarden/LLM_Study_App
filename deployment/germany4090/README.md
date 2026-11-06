# Germany VPS API entry for the 4090

Approved application path:

```text
iPhone / simulator -> https://213.145.87.189:18443
  -> Germany VPS 127.0.0.1:18426 (restricted persistent SSH forward)
  -> 4090 API 127.0.0.1:18126
```

The Mac and Windows are used only for deployment/administration. They are not
required for application requests. OCR, resident CUDA PP-DocLayout, BGE and
Whisper continue running on the 4090.

The VPS runs `../direct4090/public_api_proxy.py` with
`CLOUDPATH_PUBLIC_API_UPSTREAM=http://127.0.0.1:18426`.
Requests and responses stream through the proxy; upload payloads are not
buffered in full. Local development login is blocked at the public entry.
Native email authentication and individual session cookies are preserved.

Install the service files under /etc/systemd/system on the VPS. The runtime
directory is /root/cloudpath-ops/germany4090-public, with an isolated Python
environment containing FastAPI, HTTPX, Uvicorn and Certbot >= 5.3.
Provision an API-only SSH key on the VPS and authorize it on the 4090 with:
`restrict,port-forwarding,permitopen="127.0.0.1:18126",command="/usr/bin/false"`.
Keep all private keys and runtime credentials outside Git.

The HTTPS IP certificate is issued by Let's Encrypt using Certbot's standalone
HTTP-01 authenticator, `--preferred-profile shortlived --ip-address 213.145.87.189`.
Its live path is /etc/letsencrypt/live/cloudpath-vps-ip/. The included timer
checks renewal twice daily and restarts only the public API entry after renewal.
Port 80 must remain reachable for certificate validation; port 18443 serves the
API. Preserve the existing VPN service on port 443.

The client retires saved Mac/loopback addresses automatically. A Keychain
binding retains the verified 4090 account's original local-data origin, so
courses, notes, deletion tombstones and generation request records remain in
their existing scope after this endpoint move.
