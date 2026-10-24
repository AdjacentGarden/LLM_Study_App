"""Isolated no-provider process entry; opens durable logs before starting Uvicorn."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', required=True, choices=['adaptive_learning.api.app:app'])
    parser.add_argument('--host', required=True, choices=['127.0.0.1'])
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--log', required=True, type=Path)
    args = parser.parse_args()
    if args.port == 8100 or not 1024 <= args.port <= 65535:
        raise SystemExit('Isolated candidate port must differ from 8100')
    args.log.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(descriptor, 1)
    os.dup2(descriptor, 2)
    os.close(descriptor)
    # No .env lookup or paid/provider setup is performed here.
    import uvicorn
    uvicorn.run(args.app, host=args.host, port=args.port, workers=1,
                proxy_headers=False, access_log=False)


if __name__ == '__main__':
    main()
