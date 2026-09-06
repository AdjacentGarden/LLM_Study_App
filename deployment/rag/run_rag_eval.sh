#!/usr/bin/env bash
set -euo pipefail

ROOT=/data1/zhenghang/adaptive-book-ocr
RAG_ROOT="$ROOT/rag-eval"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-3}"
export PYTHONPATH="$ROOT/runtime-patches/lib/python3.12/site-packages:$ROOT/runtime/lib/python3.12/site-packages"
export HF_HOME="$ROOT/cache/huggingface"
export XDG_CACHE_HOME="$ROOT/cache/xdg"
export TMPDIR="$ROOT/tmp"
export TOKENIZERS_PARALLELISM=false

mkdir -p "$RAG_ROOT/index" "$RAG_ROOT/results" "$TMPDIR"

/home/zhenghang/download/enter/bin/python "$ROOT/app/deployment/rag/rag_eval.py" index \
  --pages "$ROOT/output/biology-full/normalized/pages.jsonl" \
  --embedding-model "$RAG_ROOT/models/bge-small-zh-v1.5" \
  --output "$RAG_ROOT/index"

/home/zhenghang/download/enter/bin/python "$ROOT/app/deployment/rag/rag_eval.py" evaluate \
  --index "$RAG_ROOT/index" \
  --cases "$ROOT/app/deployment/rag/eval_cases.json" \
  --embedding-model "$RAG_ROOT/models/bge-small-zh-v1.5" \
  --reranker-model "$RAG_ROOT/models/bge-reranker-base" \
  --output "$RAG_ROOT/results/retrieval_eval.json"
