#!/usr/bin/env bash
set -euo pipefail

base_url="${BOOKCOURSE_PUBLIC_URL:?Set BOOKCOURSE_PUBLIC_URL, for example https://bookcourse.example.com}"
internal_url="${BOOKCOURSE_INTERNAL_URL:-http://127.0.0.1:8000}"
cookie_file="${BOOKCOURSE_SMOKE_COOKIE_FILE:?Set BOOKCOURSE_SMOKE_COOKIE_FILE to a release-test SSO cookie jar}"
expected_user="${BOOKCOURSE_SMOKE_EXPECTED_USER_ID:?Set BOOKCOURSE_SMOKE_EXPECTED_USER_ID to the non-admin release-test identity}"
smoke_dir="$(mktemp -d)"
trap 'rm -rf "${smoke_dir}"' EXIT
health_file="${smoke_dir}/health.json"
ready_file="${smoke_dir}/ready.json"
session_file="${smoke_dir}/session.json"
spoof_session_file="${smoke_dir}/spoof-session.json"
capabilities_file="${smoke_dir}/runtime-capabilities.json"

[[ -f "${cookie_file}" ]]
[[ "${base_url}" == https://* ]]
curl --fail --silent --show-error "${internal_url}/api/health" >"${health_file}"
curl --fail --silent --show-error "${internal_url}/api/ready" >"${ready_file}"
curl --fail --silent --show-error "${base_url}/manifest.webmanifest" >/dev/null
curl --fail --silent --show-error --cookie "${cookie_file}" \
  "${base_url}/api/session" >"${session_file}"
curl --fail --silent --show-error --cookie "${cookie_file}" \
  --header 'X-BookCourse-User-Id: forged-release-user' \
  --header 'X-BookCourse-Admin-Token: forged-release-admin-token' \
  "${base_url}/api/session" >"${spoof_session_file}"
curl --fail --silent --show-error --cookie "${cookie_file}" \
  "${base_url}/api/runtime-capabilities" >"${capabilities_file}"
negative_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --max-time 30 \
  --header "X-BookCourse-User-Id: ${expected_user}" \
  --header 'X-BookCourse-Admin-Token: forged-release-admin-token' \
  "${base_url}/api/session")"
[[ "${negative_status}" == "401" || "${negative_status}" == "403" ]]

python3 - "${health_file}" "${ready_file}" "${session_file}" "${spoof_session_file}" "${capabilities_file}" "${expected_user}" <<'PY'
import json
import sys

health = json.load(open(sys.argv[1], encoding="utf-8"))
ready = json.load(open(sys.argv[2], encoding="utf-8"))
session = json.load(open(sys.argv[3], encoding="utf-8"))
spoof_session = json.load(open(sys.argv[4], encoding="utf-8"))
capabilities = json.load(open(sys.argv[5], encoding="utf-8"))
expected_user = sys.argv[6]

if health.get("status") != "ok":
    raise SystemExit("health endpoint is not ok")
required_checks = {
    "storage",
    "persistent_state",
    "authentication",
    "vector_store",
    "parser",
    "worker",
    "production_providers",
}
checks = ready.get("checks")
if ready.get("status") != "ready" or not isinstance(checks, dict):
    raise SystemExit("readiness endpoint is not ready")
if not required_checks.issubset(checks) or any(checks[key] != "ok" for key in required_checks):
    raise SystemExit("readiness is missing a required production check")

if session.get("user_id") != expected_user:
    raise SystemExit("authenticated /api/session returned the wrong verified user_id")
if session.get("is_admin") is not False:
    raise SystemExit("release smoke identity must be a non-administrator")
if spoof_session != session:
    raise SystemExit("trusted edge did not overwrite spoofed user/admin identity headers")

expected_capabilities = {
    "auth_mode": "strict",
    "parser_provider": "mineru",
    "rag_index_provider": "pgvector",
    "embedding_provider": "bge_m3",
    "reranker_provider": "bge",
    "reranker_model": "BAAI/bge-reranker-v2-m3",
    "reranker_revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
    "reranker_fail_open": False,
    "image_provider": "openai_compatible",
    "worker_enabled": True,
}
for key, expected in expected_capabilities.items():
    if capabilities.get(key) != expected:
        raise SystemExit(f"runtime capability {key} is not production-safe")
if not str(capabilities.get("reranker_device", "")).startswith("cuda"):
    raise SystemExit("reranker is not configured for CUDA")
real_llm_providers = {"deepseek", "openai_compatible"}
if capabilities.get("lesson_provider") not in real_llm_providers:
    raise SystemExit("lesson provider is not a real production provider")
if capabilities.get("rag_answer_provider") not in real_llm_providers:
    raise SystemExit("RAG answer provider is not a real production provider")
PY
headers="$(curl --fail --silent --show-error --head "${base_url}/")"
grep -qi '^strict-transport-security:' <<<"${headers}"
grep -qi '^content-security-policy:' <<<"${headers}"
echo "BookCourse unauthenticated session gate: HTTP ${negative_status}"
echo "BookCourse production smoke: PASS"
