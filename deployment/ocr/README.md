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
CUDA_VISIBLE_DEVICES=1 /data1/zhenghang/adaptive-book-ocr/app/deployment/ocr/run_biology_ocr.sh
```
