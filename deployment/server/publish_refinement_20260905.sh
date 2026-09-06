#!/usr/bin/env bash
# Targeted release; no database, secret, OCR model, or book-file replacement.
set -euo pipefail
app_root=/data1/zhenghang/adaptive-book-ocr/app
stage_root=/data1/zhenghang/adaptive-book-ocr/app/tmp/deep-refinement.Yrpv2g
backup=/data1/zhenghang/adaptive-book-ocr/release-backups/pre-deep-refinement-20260905.tar.gz
[[ -f "$stage_root/frontend/dist/index.html" && -f "$app_root/backend/src/adaptive_learning/api/app.py" ]]
[[ ! -e "$backup" ]] || { echo 'Backup already exists; refusing to overwrite it'; exit 1; }
tar -czf "$backup" -C "$app_root" frontend/src frontend/dist backend/src
files=(
  backend/src/adaptive_learning/api/app.py
  backend/src/adaptive_learning/api/qa_dependency.py
  backend/src/adaptive_learning/api/schemas.py
  backend/src/adaptive_learning/llm/client.py
  backend/src/adaptive_learning/personalization/generator.py
  backend/src/adaptive_learning/rag/grounded_qa.py
  backend/src/adaptive_learning/rag/service.py
  backend/tests/test_llm_client.py
  backend/tests/test_qa_api.py
  backend/tests/test_qa_resilience.py
  backend/tests/test_semantic_review.py
  frontend/src/App.tsx
  frontend/src/api/client.ts
  frontend/src/api/transport.ts
  frontend/src/components/bookContext.ts
  frontend/src/components/ErrorBoundary.tsx
  frontend/src/components/PracticeFeedback.tsx
  frontend/src/components/LearningHome.tsx
  frontend/src/components/TutorChat.tsx
  frontend/src/components/FlashcardFace.tsx
  frontend/src/components/useAnimatedView.ts
  frontend/src/styles/refinement.css
  frontend/src/main.tsx
  frontend/tests/transport.test.ts
  frontend/tests/bookContext.test.ts
  deployment/rag/deep_quality_eval.py
)
for file in "${files[@]}"; do
  [[ -f "$stage_root/$file" ]]
done
# Stop only the verified application process, after tests and backup are complete.
bash "$app_root/deployment/server/run_backend.sh" stop
for file in "${files[@]}"; do
  mkdir -p "$(dirname "$app_root/$file")"
  cp "$stage_root/$file" "$app_root/$file"
done
# Keep old hashed assets available to already-open browser sessions.
cp -a "$stage_root/frontend/dist/assets/." "$app_root/frontend/dist/assets/"
cp "$stage_root/frontend/dist/index.html" "$app_root/frontend/dist/.refinement-index.next"
mv "$app_root/frontend/dist/.refinement-index.next" "$app_root/frontend/dist/index.html"
if ! bash "$app_root/deployment/server/run_backend.sh" start; then
  echo 'Startup failed. Restore source/static backup; leave learning data untouched.' >&2
  bash "$app_root/deployment/server/run_backend.sh" stop
  tar -xzf "$backup" -C "$app_root"
  bash "$app_root/deployment/server/run_backend.sh" start
  exit 1
fi
curl -fsS http://127.0.0.1:8100/api/rag/health
echo
echo "Frontend/backend released; backup: $backup"
