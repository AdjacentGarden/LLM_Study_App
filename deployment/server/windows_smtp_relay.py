"""Fixed-target, byte-transparent QQ SMTP TLS tunnel over pinned SSH.

Only Linux loopback 18465 is exposed; every channel connects smtp.qq.com:465.
The backend performs TLS with smtp.qq.com as its authenticated server name.
Reuse the existing protected DPAPI credential file; never copy plaintext secrets.
"""
from __future__ import annotations

import argparse
import base64
import logging
import select
import signal
import socket
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from windows_llm_relay import HOST_KEY, SERVER, bounded_request, load_credentials

REMOTE_BIND = "127.0.0.1"
REMOTE_PORT = 18465
SMTP_TARGET = ("smtp.qq.com", 465)
STOP = threading.Event()
LOG = logging.getLogger("cloudpath.smtp-relay")


def relay(channel, session_stop, slots):
    upstream = None
    try:
        upstream = socket.create_connection(SMTP_TARGET, timeout=10)
        upstream.settimeout(15)
        channel.settimeout(15)
        last_activity = time.monotonic()
        while not STOP.is_set() and not session_stop.is_set():
            readable, _, _ = select.select([channel, upstream], [], [], 1)
            if not readable and time.monotonic() - last_activity > 120:
                break
            for source in readable:
                block = source.recv(65536)
                if not block:
                    return
                (upstream if source is channel else channel).sendall(block)
                last_activity = time.monotonic()
    except Exception as error:  # noqa: BLE001 - never log TLS/application bytes.
        LOG.warning("SMTP channel ended (%s)", type(error).__name__)
    finally:
        if upstream is not None:
            upstream.close()
        channel.close()
        slots.release()


def serve(credentials):
    import paramiko

    pinned = paramiko.Ed25519Key(data=base64.b64decode(HOST_KEY))
    backoff = 2
    slots = threading.BoundedSemaphore(4)
    while not STOP.is_set():
        client = paramiko.SSHClient()
        client.get_host_keys().add(SERVER, "ssh-ed25519", pinned)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        session_stop = threading.Event()
        started = time.monotonic()
        try:
            client.connect(SERVER, port=22, username=credentials["username"],
                           password=credentials["password"], timeout=15,
                           banner_timeout=15, auth_timeout=20,
                           look_for_keys=False, allow_agent=False)
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise ConnectionError("SSH unavailable")
            port = bounded_request(transport, lambda current=transport: current.request_port_forward(
                REMOTE_BIND, REMOTE_PORT))
            if port != REMOTE_PORT:
                raise ConnectionError("Unexpected bind")
            LOG.info("Fixed SMTP tunnel active on server loopback port %d", port)
            heartbeat_at = time.monotonic() + 20
            while not STOP.is_set() and transport.is_active():
                channel = transport.accept(timeout=1)
                if channel is not None:
                    if slots.acquire(blocking=False):
                        threading.Thread(target=relay, args=(channel, session_stop, slots),
                                         daemon=True).start()
                    else:
                        channel.close()
                if time.monotonic() >= heartbeat_at:
                    bounded_request(transport, lambda current=transport: current.global_request(
                        "keepalive@openssh.com", wait=True), timeout=10)
                    heartbeat_at = time.monotonic() + 20
            if not STOP.is_set():
                raise ConnectionError("SSH disconnected")
        except Exception as error:  # noqa: BLE001 - redact credential and transport details.
            LOG.warning("SMTP tunnel reconnect required (%s)", type(error).__name__)
        finally:
            session_stop.set()
            client.close()
        if time.monotonic() - started > 60:
            backoff = 2
        if not STOP.is_set():
            STOP.wait(backoff)
            backoff = min(60, backoff * 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    args = parser.parse_args()
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    LOG.setLevel(logging.INFO)
    handler = RotatingFileHandler(args.log_file, maxBytes=2_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    logging.getLogger("paramiko").setLevel(logging.CRITICAL)
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: STOP.set())
    serve(load_credentials(args.credential_file))


if __name__ == "__main__":
    main()
