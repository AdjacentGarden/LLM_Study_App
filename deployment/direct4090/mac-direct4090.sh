#!/bin/bash
set -euo pipefail
exec /usr/bin/ssh -F /dev/null -N \
  -p 36243 -i "${HOME}/.ssh/cloudpath-direct4090" \
  -o IdentitiesOnly=yes -o BatchMode=yes \
  -o "UserKnownHostsFile=${HOME}/.ssh/cloudpath-direct4090-known_hosts" \
  -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=20 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:18100:127.0.0.1:18126 root@connect.bjb1.seetacloud.com
