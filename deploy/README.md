# Production deployment gate

This directory is a production reference, not a file to copy blindly. The
release remains closed until all required secrets, TLS files, and providers are
provisioned on the target host.

1. Install the backend into `/srv/bookcourse` with Python 3.11 and
   `cd /srv/bookcourse/backend && uv sync --frozen --extra rag --extra ocr --no-dev`.
   This creates `/srv/bookcourse/backend/.venv`, the same environment used by
   the systemd unit and canary. The lock selects PyTorch 2.13's official CUDA
   12.6 wheel on production Linux (desktop platforms use the CPU wheel); do not
   replace its index with PyPI's default CUDA 13 build. The checked-in
   cross-platform `backend/uv.lock` pins artifacts and hashes for both
   production extras. Install the environment into
   `/etc/bookcourse/bookcourse.env` (mode 600) and the systemd unit. Verify the
   matching `backend/sbom.cdx.json` before promotion.
   Audit both `requirements.lock` and
   `security-audit-local-versions.txt`; the second maps PyTorch's official
   `2.13.0+cu126` local build to the upstream `2.13.0` vulnerability record.
2. Run the API only on `127.0.0.1:8000`; Nginx is the sole public entry point.
3. Run an identity proxy on `127.0.0.1:4180`. Its `/verify` response must return
   a stable, authenticated `X-Verified-User`; it must never trust a browser
   supplied identity header.
4. Install two distinct random secrets in the backend environment and
   `proxy-secrets.conf`, plus a valid TLS chain under `/etc/bookcourse/tls`.
5. Before the first start, apply the pgvector schema through the approved DBA
   change process. Declare the target DSN, take a verified database backup,
   review `backend/migrations/001_pgvector_v2.sql`, then run:
   `psql "$BOOKCOURSE_DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/001_pgvector_v2.sql`.
   Verify with `SELECT extversion FROM pg_extension WHERE extname='vector';`
   and `SELECT to_regclass('public.rag_index_state'),
   to_regclass('public.rag_chunk_vectors');`. The migration is
   additive; rollback is restore-from-backup after stopping API traffic, not an
   ad-hoc destructive down migration.
6. Pre-download the frozen BGE-M3 snapshot and reranker into
   `/var/lib/bookcourse/models` as the `bookcourse` user:

   ```sh
   cd /srv/bookcourse/backend
   sudo -u bookcourse env HF_HOME=/var/lib/bookcourse/models \
     .venv/bin/hf download BAAI/bge-m3 \
     --revision 5617a9f61b028005a4858fdac845db406aefb181
   sudo -u bookcourse env HF_HOME=/var/lib/bookcourse/models \
     .venv/bin/hf download BAAI/bge-reranker-v2-m3 \
     --revision 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e
   sudo -u bookcourse env HF_HOME=/var/lib/bookcourse/models \
     HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
     .venv/bin/python ../deploy/a100-canary.py
   ```

   Retain the canary JSON in the release record. The canary refuses to run
   outside the service's exact cache root or with network access enabled. This
   prevents a cold model download or incompatible CUDA runtime from surfacing
   on the first learner request. Retain `deploy/model-manifest.json` with the
   model snapshots and record their resolved artifact hashes. The systemd unit
   runs with Hugging Face and Transformers offline mode enabled, so startup
   must fail rather than fetch a floating or unreviewed artifact.
7. Before building the frontend, populate the server-owned community library as the service
   account (the PDFs are runtime data and must never be bundled into the web
   application):

   ```sh
   cd /srv/bookcourse/backend
   sudo -u bookcourse .venv/bin/python -m app.community.prefetch
   ```

   Confirm `/api/community/books` reports `server_cached=true` for every entry.
   Then build the frontend, commit the complete candidate, create exactly one signed
   annotated release tag, and keep the worktree clean. Create a deterministic
   manifest outside the repository:

   ```sh
   python3 deploy/release-manifest.py create /var/lib/bookcourse/release-evidence/release-manifest.json
   ```

   The command refuses dirty or untagged source and binds every tracked file
   plus every deployed frontend file to SHA-256 digests.
8. Run the complete target gate as root with a short-lived, non-administrator
   release-test SSO cookie jar. Never commit or attach that cookie jar:

   ```sh
   install -d -o root -g root -m 0700 /var/lib/bookcourse-release-evidence
   chmod 0600 /run/bookcourse-release-cookie.txt
   BOOKCOURSE_PUBLIC_URL=https://your-host \
   BOOKCOURSE_SMOKE_COOKIE_FILE=/run/bookcourse-release-cookie.txt \
   BOOKCOURSE_SMOKE_EXPECTED_USER_ID=bookcourse-release-test \
   BOOKCOURSE_SMOKE_PDF=/root/release_readiness_fixture.pdf \
   BOOKCOURSE_RELEASE_RESTART_CONFIRMED=YES \
     ./deploy/collect-target-evidence.sh /var/lib/bookcourse-release-evidence/v0.1.0
   ```

   This validates Nginx, loopback-only API binding, systemd, authenticated SSO
   session identity and unauthenticated spoof rejection, a controlled API
   restart bound to the manifested source, a real PDF upload/worker
   parse/pgvector+BGE/CUDA grounded RAG round trip, and one grounded lesson and
   validated image through the configured production providers,
   health/readiness, the exact locked environment and offline A100 models, and
   the release manifest. The evidence directory must be new and live below a
   root-owned, non-writable-by-service parent. It produces checksummed evidence
   for the final five-agent review. Destroy the SSO cookie immediately afterward.

`/api/ready` intentionally returns 503 in production if the worker, real AI
providers, database schema, parser, authentication proxy, or storage are not
ready. Do not route traffic until the smoke script passes.
