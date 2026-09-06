#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
evidence_dir="${1:?Usage: collect-target-evidence.sh /absolute/evidence-directory}"
public_url="${BOOKCOURSE_PUBLIC_URL:?Set BOOKCOURSE_PUBLIC_URL to the production HTTPS origin}"
cookie_file="${BOOKCOURSE_SMOKE_COOKIE_FILE:?Set BOOKCOURSE_SMOKE_COOKIE_FILE to a release-test SSO cookie jar}"
expected_user="${BOOKCOURSE_SMOKE_EXPECTED_USER_ID:?Set BOOKCOURSE_SMOKE_EXPECTED_USER_ID to the non-admin release-test identity}"
pdf_fixture="${BOOKCOURSE_SMOKE_PDF:?Set BOOKCOURSE_SMOKE_PDF to the real release PDF fixture}"
internal_url="${BOOKCOURSE_INTERNAL_URL:-http://127.0.0.1:8000}"
restart_confirmed="${BOOKCOURSE_RELEASE_RESTART_CONFIRMED:-}"

if [[ "${repo_root}" != "/srv/bookcourse" ]]; then
  echo "Target evidence must run from the deployed /srv/bookcourse candidate" >&2
  exit 1
fi

if [[ "${evidence_dir}" != /* ]]; then
  echo "Evidence directory must be an absolute path" >&2
  exit 1
fi
if [[ ! -f "${cookie_file}" ]]; then
  echo "SSO cookie file does not exist: ${cookie_file}" >&2
  exit 1
fi
if [[ "${public_url}" != https://* ]]; then
  echo "BOOKCOURSE_PUBLIC_URL must use HTTPS" >&2
  exit 1
fi
cookie_mode="$(stat -c '%a' "${cookie_file}")"
if (( (8#${cookie_mode} & 8#077) != 0 )); then
  echo "SSO cookie file must not be accessible by group or other users" >&2
  exit 1
fi
if [[ ! -f "${pdf_fixture}" ]]; then
  echo "PDF fixture does not exist: ${pdf_fixture}" >&2
  exit 1
fi
if [[ "${restart_confirmed}" != "YES" ]]; then
  echo "Set BOOKCOURSE_RELEASE_RESTART_CONFIRMED=YES to bind the live process to this candidate" >&2
  exit 1
fi
if [[ -e "${evidence_dir}" ]]; then
  echo "Evidence directory must not already exist: ${evidence_dir}" >&2
  exit 1
fi

evidence_parent="$(realpath -e "$(dirname "${evidence_dir}")")"
parent_uid="$(stat -c '%u' "${evidence_parent}")"
parent_mode="$(stat -c '%a' "${evidence_parent}")"
if [[ "${parent_uid}" != "0" ]] || (( (8#${parent_mode} & 8#022) != 0 )); then
  echo "Evidence parent must be root-owned and not group/other writable" >&2
  exit 1
fi
case "${evidence_parent}/" in
  "${repo_root}/"*|/srv/bookcourse/*|/var/lib/bookcourse/*)
    echo "Evidence must be outside the repository and service-writable roots" >&2
    exit 1
    ;;
esac

umask 077
mkdir -- "${evidence_dir}"
chmod 0700 "${evidence_dir}"
evidence_dir="$(realpath -e "${evidence_dir}")"
set -o noclobber
cd "${repo_root}"

python3 deploy/release-manifest.py create "${evidence_dir}/release-manifest.json"
git status --porcelain=v1 --untracked-files=all >"${evidence_dir}/git-status.txt"
git show --no-patch --format=fuller HEAD >"${evidence_dir}/git-commit.txt"
git tag --points-at HEAD >"${evidence_dir}/git-tag.txt"

nvidia-smi --query-gpu=name,uuid,driver_version,memory.total,compute_cap \
  --format=csv,noheader >"${evidence_dir}/nvidia-smi.csv"
nvidia-smi >"${evidence_dir}/nvidia-smi.txt"
nginx -t >"${evidence_dir}/nginx-test.txt" 2>&1
systemctl restart bookcourse-api.service
systemctl is-active bookcourse-api.service >"${evidence_dir}/systemd-active.txt"
systemctl show bookcourse-api.service \
  --property=Id,LoadState,ActiveState,SubState,FragmentPath,User,Group,WorkingDirectory,EnvironmentFiles,MainPID \
  >"${evidence_dir}/systemd-unit-state.txt"
service_user="$(systemctl show bookcourse-api.service --property=User --value)"
service_group="$(systemctl show bookcourse-api.service --property=Group --value)"
service_workdir="$(systemctl show bookcourse-api.service --property=WorkingDirectory --value)"
service_pid="$(systemctl show bookcourse-api.service --property=MainPID --value)"
[[ "${service_user}" == "bookcourse" ]]
[[ "${service_group}" == "bookcourse" ]]
[[ "${service_workdir}" == "/srv/bookcourse/backend" ]]
[[ "${service_pid}" =~ ^[1-9][0-9]*$ ]]
[[ "$(readlink -e "/proc/${service_pid}/cwd")" == "/srv/bookcourse/backend" ]]
service_command="$(tr '\0' ' ' <"/proc/${service_pid}/cmdline")"
[[ "${service_command}" == *"/srv/bookcourse/backend/.venv/bin/uvicorn"* ]]
[[ "${service_command}" == *"--host 127.0.0.1"* ]]
{
  printf 'pid=%s\n' "${service_pid}"
  printf 'cwd=%s\n' "${service_workdir}"
  sha256sum "/proc/${service_pid}/exe"
} >"${evidence_dir}/live-process.txt"
ss -ltnp >"${evidence_dir}/listening-sockets.txt"
grep -Eq '127\.0\.0\.1:8000' "${evidence_dir}/listening-sockets.txt"
if grep -Eq '(0\.0\.0\.0|\*|\[::\]|::):8000' "${evidence_dir}/listening-sockets.txt"; then
  echo "Backend port 8000 is exposed beyond loopback" >&2
  exit 1
fi

(
  cd backend
  uv sync --frozen --extra rag --extra ocr --no-dev --check
) >"${evidence_dir}/uv-sync-check.txt" 2>&1
uv pip check --python backend/.venv/bin/python >"${evidence_dir}/uv-pip-check.txt"
uv pip list --python backend/.venv/bin/python --format json >"${evidence_dir}/installed-packages.json"

runuser -u bookcourse -- env \
  HF_HOME=/var/lib/bookcourse/models \
  TRANSFORMERS_CACHE=/var/lib/bookcourse/models \
  HF_HUB_OFFLINE=1 \
  TRANSFORMERS_OFFLINE=1 \
  /srv/bookcourse/backend/.venv/bin/python \
  /srv/bookcourse/deploy/a100-canary.py >"${evidence_dir}/a100-canary.json"

ready_payload=""
for _ in $(seq 1 60); do
  if ready_payload="$(curl --fail --silent --show-error "${internal_url}/api/ready" 2>/dev/null)"; then
    break
  fi
  sleep 2
done
[[ "${ready_payload}" == *'"status":"ready"'* ]]
curl --fail --silent --show-error "${internal_url}/api/health" >"${evidence_dir}/health.json"
printf '%s\n' "${ready_payload}" >"${evidence_dir}/ready.json"
curl --fail --silent --show-error --cookie "${cookie_file}" \
  "${public_url}/api/runtime-capabilities" >"${evidence_dir}/runtime-capabilities.json"

BOOKCOURSE_PUBLIC_URL="${public_url}" \
BOOKCOURSE_INTERNAL_URL="${internal_url}" \
BOOKCOURSE_SMOKE_COOKIE_FILE="${cookie_file}" \
BOOKCOURSE_SMOKE_EXPECTED_USER_ID="${expected_user}" \
  ./deploy/release-smoke.sh | tee "${evidence_dir}/release-smoke.txt"

python3 deploy/production-pdf-canary.py \
  --base-url "${public_url}" \
  --cookie-file "${cookie_file}" \
  --pdf "${pdf_fixture}" \
  --expected-user-id "${expected_user}" \
  --expected-token ORCHID-742 \
  --output "${evidence_dir}/production-pdf-canary.json"

python3 deploy/release-manifest.py verify "${evidence_dir}/release-manifest.json"
(
  cd "${evidence_dir}"
  find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum >SHA256SUMS
)

echo "BookCourse target evidence collection: PASS"
