#!/usr/bin/env bash
set -euo pipefail

ROOT=/data1/zhenghang/adaptive-book-ocr
SOURCE="$ROOT/input/biology-compulsory-2.pdf"
OUTPUT="$ROOT/output/biology-full"

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-2}"
export MINERU_DEVICE_MODE=cuda
export MINERU_MODEL_SOURCE=local
export MINERU_TOOLS_CONFIG_JSON="$ROOT/config/mineru.json"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export TOKENIZERS_PARALLELISM=false
export HF_HOME="$ROOT/cache/huggingface"
export MODELSCOPE_CACHE="$ROOT/cache/modelscope"
export TORCH_HOME="$ROOT/cache/torch"
export XDG_CACHE_HOME="$ROOT/cache/xdg"
export TMPDIR="$ROOT/tmp"
export PYTHONPATH="$ROOT/runtime-patches/lib/python3.12/site-packages:$ROOT/runtime/lib/python3.12/site-packages:$ROOT/app/backend/src"

mkdir -p "$OUTPUT" "$HF_HOME" "$MODELSCOPE_CACHE" "$TORCH_HOME" "$XDG_CACHE_HOME" "$TMPDIR"

exec /home/zhenghang/download/enter/bin/python -m adaptive_learning.ingestion.ocr_job \
  --source "$SOURCE" \
  --output "$OUTPUT" \
  --backend vlm-auto-engine \
  --language ch
