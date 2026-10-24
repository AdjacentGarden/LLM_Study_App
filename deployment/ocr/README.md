# 4090 OCR runner

This deployment is intentionally isolated from the application services. Every mutable file,
model, cache and output lives below `/data1/zhenghang/adaptive-book-ocr`.

The runner uses MinerU's `vlm-auto-engine` backend with a local MinerU2.5 VLM. This preserves the
high-accuracy page-understanding path while staying within one 24 GB RTX 4090. It writes raw
MinerU artifacts plus normalized per-page JSONL, full Markdown,
an anomaly-oriented quality report and SHA-256 checksums.

The Linux packages are built for CPython 3.13, staged under
`runtime/lib/python3.12/site-packages` by the cross-platform installer, and executed with the
server's existing Python 3.13 interpreter. This avoids writing another interpreter to the nearly
full system disk.

Compatibility pins for LMDeploy (`transformers==4.56.1`) and the NumPy-compatible scientific
stack are isolated in `runtime-patches` and precede the base runtime on `PYTHONPATH`.

Run:

```bash
python3 /data1/zhenghang/adaptive-book-ocr/app/deployment/server/ocr_resident_service.py start
/data1/zhenghang/adaptive-book-ocr/app/deployment/ocr/run_biology_ocr.sh
```

The deployed `config/gpu.env` selects one physical GPU and sets
`MINERU_API_URL=http://127.0.0.1:18200`. The application and manual OCR runner send
jobs to that loopback service. Its model is preloaded once and retained between
jobs; the client still writes a separate, auditable output directory per job.
The backend startup script ensures this service is running before accepting work.

The service uses one API process, one concurrent request, and the profile's
bounded LMDeploy engine arguments (`cache-max-entry-count=0.25`,
`max-batch-size=4`). Engine arguments are applied at service startup. Job clients
do not change the resident engine or start another model when the service fails.

Use `ocr_resident_service.py status`, `stop`, or `restart` to manage the service.
Its PID and log live under `run/ocr-resident.pid` and `logs/ocr-resident.log`.
Stopping the backend leaves this independently managed OCR service running.
Temporary API task outputs are separate from final OCR artifacts under
`data/tmp/mineru-resident`; completed tasks expire after 24 hours. On service
restart, older orphaned task directories may need operator cleanup in that
temporary directory. Final OCR outputs must not be removed by that cleanup.

When `MINERU_API_URL` is unset, the application retains the per-job local MinerU
mode. Configured URLs are restricted to loopback HTTP addresses.
