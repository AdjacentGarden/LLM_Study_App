#!/bin/bash
set -euo pipefail
cloudpath_vps_host="${CLOUDPATH_VPS_HOST:-213.145.87.189}"
cloudpath_vps_user="${CLOUDPATH_VPS_USER:-cprelay}"
cloudpath_relay_port="${CLOUDPATH_RELAY_PORT:-18100}"
exec /usr/bin/ssh -N \
  -i "${HOME}/.ssh/cprelay_ed25519" \
  -o IdentitiesOnly=yes -o BatchMode=yes \
  -o "UserKnownHostsFile=${HOME}/.ssh/cprelay_known_hosts" \
  -o StrictHostKeyChecking=yes \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -L "127.0.0.1:${cloudpath_relay_port}:127.0.0.1:${cloudpath_relay_port}" \
  "${cloudpath_vps_user}@${cloudpath_vps_host}"
